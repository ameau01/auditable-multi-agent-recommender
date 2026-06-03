# Architecture

This file is the **how**. The **why** lives in the [README](README.md) under "The problem" — read that first if you have not. The three constraints the problem imposes (auditability, cross-tier causation, and zero-execution) are what every choice below answers.

Two structural concerns sit perpendicular to each other here:

**Agent topology**, who reasons about what, in what sequence.
**Harness layering**, what structure, safety, and observability properties run across every agent.

- Per-agent detail lives in [docs/agents.md](docs/agents.md). 
- Per-harness detail lives in [docs/harnesses.md](docs/harnesses.md).

## Design principles

Seven commitments shape every other decision.

1. **Recommender, not executor** The system never changes infrastructure state. Every recommendation routes to a human.
2. **Multi-agent by necessity** Earned, not decorative. Each agent owns a strictly bounded scope to ensure deep analysis. A single agent processing all telemetry at once produces shallow results. Our hierarchical network structurally enforces these narrow boundaries.
3. **Accountability over adversarial defense** Since the system takes no external user input, prompt injection is not a threat. We focus entirely on reasoning quality, consistency, and auditability.
4. **Deliberate synthetic data** Establishing strict ground truth requires hand-crafted scenarios. The dataset is published at [`ameau01/synthesized-cloud-optimization-recommendations`](https://huggingface.co/datasets/ameau01/synthesized-cloud-optimization-recommendations) on Hugging Face.
5. **Harnesses provide properties, not defenses** Harness layers are designed to enforce structure, safety, and observability. They are not a checklist of security defenses mapped against hypothetical threats.
6. **Model specialization over scale** We use Haiku for the high-volume, bounded specialist turns, and Sonnet for the single, complex Evaluator synthesis. Cost and capability are matched exactly to the workload.
7. **Trade-offs are part of the deliverable** Every architectural decision has rejected alternatives. That reasoning is explicitly tracked in [docs/decisions.md](docs/decisions.md).

The deeper rationale for each principle is in [docs/decisions.md](docs/decisions.md) and the sections below.

## System Overview

```mermaid
flowchart TB
    %% Centered main flow — the stages a cycle moves through
    TR([Review trigger<br/><i>app-name + optional alert</i>])
    SUP[Supervisor<br/><i>router · decides next step · decides when to complete</i>]
    SM[System Mapper<br/><i>Terraform → tier graph</i>]

    %% Three tier-bounded specialists, dispatched in parallel
    SP_C[Compute Analyst<br/><i>ReAct · compute tier</i>]
    SP_D[Data Layer Analyst<br/><i>ReAct · database + cache</i>]
    SP_N[Network Analyst<br/><i>ReAct · network tier</i>]

    EV[Cross-Tier Evaluator<br/><i>drift-check + synthesis</i>]
    RP(["Review Packet<br/><i>recommendation + evidence chain</i>"])
    H(["Human-in-the-loop (HITL)<br/><i>approve · reject · defer</i>"])

    %% Main vertical sequence — middle specialist declared first as parent
    %% of EV so dagre places EV on the centerline.
    TR --> SUP
    SUP --> SP_D
    SUP --> SP_C
    SUP --> SP_N
    SP_D --> EV
    SP_C --> EV
    SP_N --> EV
    EV --> RP
    RP --> H

    %% Perpendicular interaction — System Mapper is a worker the Supervisor
    %% dispatches once per cycle to map the application's tier graph.
    SUP <-. dispatch · tier topology .-> SM

    classDef center fill:#eef1ff,stroke:#4a5fff,stroke-width:1.5px,color:#111
    classDef terminus fill:#fff,stroke:#222,stroke-width:1.5px,color:#000
    class SUP,SP_C,SP_D,SP_N,EV,SM center
    class TR,RP,H terminus
```

*Blue boxes are agents; oval endpoints are external boundaries (trigger in, deliverable out, human review). The four harnesses are cross-cutting concerns covered by the next section; the data layer that specialists query is documented separately in [docs/mcp-server.md](docs/mcp-server.md).*

**Reading the diagram.** The vertical sequence — trigger, Supervisor, three tier-bounded Specialists in parallel, Cross-Tier Evaluator, Review Packet, Human-in-the-loop — is the conceptual flow of one review cycle. The Supervisor fans out to the three Specialists whose tiers the System Mapper detected; their findings fan back in to the Cross-Tier Evaluator for drift-check and synthesis. The fan-out / fan-in shape is the coordination story — many independent specialist agents, one synthesized conclusion. System Mapper sits perpendicular because the Supervisor dispatches it once per cycle to map the application's tier graph, then control returns to the Supervisor — it's a worker the Supervisor calls, not a stage in the pipeline.

**Supervisor is the only router.** Although the diagram draws the sequence as a vertical chain, the implementation routes every transition through the Supervisor: Supervisor decides whether to call System Mapper, which specialists to dispatch, when to synthesize via the Evaluator, when to hand the synthesized recommendation onward to the Action Harness gate (see the next section), and crucially — when to terminate the cycle. Every worker node returns to the Supervisor between stages; no worker can decide "we're done" on its own. The downward arrows are the conceptual sequence; the routing loop through the Supervisor is left implicit to keep the diagram readable. This is a conceptual narrative of the reasoning structure — the actual control flow and state transitions are handled by LangGraph underneath.

Every arrow crosses one or more harnesses — the next section describes them. Note that this is a logical topology, not a microservice deployment diagram. For this portfolio implementation, the system runs as a single Python process; the architectural boundaries are strictly logical, not infrastructural.

## The Four Harnesses

The harnesses are not a fifth agent. They are system-wide constraints enforced across the agents themselves and the data they read and produce.

```mermaid
flowchart TB
    %% The four harnesses — cross-cutting, not a pipeline. Each fires
    %% at a different kind of event; all four write their verdicts
    %% into the same harness_trail table.
    IH["Input Harness<br/><i>gates: trigger + app's scenario data</i><br/><i>fires: once at cycle start</i>"]
    AH["Action Harness<br/><i>gates: every tool call attempt,<br/>plus the final recommendation</i><br/><i>fires: per call</i>"]
    RH["Reasoning Harness<br/><i>gates: finding_type, evidence_refs,<br/>decision-evidence, drift verdicts</i><br/><i>fires: per structured output</i>"]
    OH["Orchestration Harness<br/><i>gates: cycle-level transitions —<br/>cycle completion legitimacy,<br/>proceed-to-evaluator</i>"]

    HT[("harness_trail<br/><i>every verdict lands here —<br/>passed · rejected · flagged · info</i>")]

    IH --> HT
    AH --> HT
    RH --> HT
    OH --> HT

    classDef harness fill:#eef1ff,stroke:#4a5fff,stroke-width:1.5px,color:#111
    classDef table fill:#fff7e6,stroke:#c08400,stroke-width:1.5px,color:#111
    class IH,AH,RH,OH harness
    class HT table
```

The harnesses are cross-cutting concerns, not a sequential pipeline — each fires at a different kind of event during one cycle. Every verdict (passed, rejected, flagged, info) lands in `harness_trail`, keyed by `check_name` and linked to the audit row it judged via `related_event_id`. The agent's substance — what it decided and what evidence it cited — lives in a separate table, `audit_records`. The two tables together let a reader reconstruct both the decision report (substance) and the enforcement report (verdicts) for any cycle. See `docs/audit-trail.md` for the table-level model and `docs/harnesses.md` for what each harness checks.

## Tier Specialist: the ReAct loop

Each specialist executes a strictly constrained ReAct loop, detailed in [docs/agents.md](docs/agents.md).

```mermaid
flowchart TB
    %% Node Definitions
    INV(["Invocation<br/>Receives tier scope & SLA target"])
    TH["Thought<br/>Formulates hypothesis & identifies required evidence"]
    AC["Action<br/>Executes read operation via scoped tool surface"]
    OB["Observation<br/>Receives structured telemetry/data"]
    SUF{"Evidence threshold<br/>met?"}
  
    %% Terminal States
    F["Issue Identified<br/>finding_type: issue_found<br/>(Includes recommendation, evidence refs & confidence)"]
    NIF["No Actionable Issue<br/>finding_type: no_issue_found<br/>OR insufficient_data"]

    %% Execution Flow
    INV --> TH --> AC --> OB --> SUF
    SUF -->|"No, requires deeper traversal"| TH
    SUF -->|"Yes, anomaly confirmed"| F
    SUF -->|"Yes, baseline healthy or data missing"| NIF
```
**Execution Boundaries:** The ReAct loop, terminal states (`issue_found` / `no_issue_found` / `insufficient_data`), and a worked cycle are detailed in [`docs/agents.md`](docs/agents.md).

## Cross-Tier Evaluator

The Evaluator has three sub-steps in sequence:

```mermaid
flowchart TB
    %% Node Definitions
    IN(["Input Payload<br/>Specialist findings, up to 3, & cross-tier mapping"])
  
    S1["Step 1: Specialist Drift-Check<br/>Validates evidence binding<br/>Identifies internal contradictions<br/>Flags unsupported claims"]
  
    S2["Step 2: Cross-Tier Interaction Mapping<br/>Identifies conflicting optimizations<br/>Maps compound effects & cross-tier dependencies"]
  
    S3["Step 3: Synthesis & Trade-off Scoring<br/>Scores cost, performance & reliability<br/>Generates final recommendation & evaluator confidence"]
  
    OUT(["Final Synthesis Output<br/>Balanced action plan, trade-off matrix, evaluator confidence,<br/>drift verdicts, & per-specialist contribution trace"])

    %% Execution Flow
    IN --> S1 --> S2 --> S3 --> OUT
```

**Evaluator Logic:** The three steps run in strict sequence with drift-check first, so a weak or contradictory finding cannot pollute the final synthesis. Full mechanics and correlated-drift handling are in [`docs/agents.md`](docs/agents.md). 

## Where Each Harness Applies

| Execution Stage | Input Harness | Reasoning Harness | Action Harness | Orchestration Harness | Audit Trail |
| --- | --- | --- | --- | --- | --- |
| **Trigger & Ingest** | Validates scenario data: schema, completeness, timestamp continuity | - | - | - | Logs trigger & scenario hash |
| **System Mapper** | Validates Terraform parsing | Verifies the topology conclusion is evidence-backed | Scopes the MCP read surface to scenario-level tools | - | Logs architecture model & analysis plan |
| **Supervisor Decisions** | - | Verifies every routing decision cites the evidence it relied on | - | Validates cycle-level transitions are legitimate (no `completed` without specialists; `failed` carries a `failed_at_stage`) | Logs routing & invocation decisions |
| **Tier Specialist ReAct** | - | Enforces structured reasoning, evidence binding & confidence scoring | Scopes the MCP read surface to the specialist's tier | - | Logs every tool call & reasoning step |
| **Cross-Tier Evaluator** | - | Enforces drift-check, synthesis & trade-off scoring | - | Verifies specialists completed before synthesis runs | Logs drift verdicts, scores & synthesis |
| **Final Recommendation Gate** | - | - | Gates well-formedness, evidence, severity & duplication | - | Logs final gate verdict |
| **HITL Decision** | - | - | - | - | Logs human approval, rejection, or deferral |

The Reasoning Harness carries the heaviest cognitive load. The Action Harness remains intentionally narrow. The Orchestration Harness gates the cycle-level transitions that no single agent owns. The audit trail — split across `audit_records` for agent substance and `harness_trail` for harness verdicts — maintains state across the entire lifecycle. The Input Harness acts strictly as the front door.
