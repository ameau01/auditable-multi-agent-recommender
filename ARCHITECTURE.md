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
    SPECS[Specialists<br/><i>Compute · Data Layer · Network</i><br/><i>ReAct, tier-bounded</i>]
    EV[Cross-Tier Evaluator<br/><i>drift-check + synthesis</i>]
    GATE{{Action Harness gate<br/><i>severity · duplication · evidence completeness</i>}}
    RP[Review Packet<br/><i>recommendation + evidence chain</i>]
    H([Human reviewer<br/><i>approve / reject / defer</i>])

    %% Side branches — perpendicular to the main flow
    SM[System Mapper<br/><i>Terraform → tier graph</i>]
    TEL[(MCP scenario data<br/><i>Terraform + 14d telemetry + sidecar</i><br/><i>validated by Input Harness</i>)]

    %% Main vertical sequence
    TR --> SUP
    SUP --> SPECS
    SPECS --> EV
    EV --> GATE
    GATE --> RP
    RP --> H

    %% Perpendicular branches (one edge each, no clutter)
    SM -. tier topology .-> SUP
    SPECS <-. tool calls .-> TEL

    classDef center fill:#eef1ff,stroke:#4a5fff,stroke-width:1.5px,color:#111
    classDef side fill:#fafafa,stroke:#999,stroke-dasharray:4 3,color:#444
    classDef terminus fill:#fff,stroke:#222,stroke-width:1.5px,color:#000
    classDef gate fill:#fffbe6,stroke:#c08400,stroke-width:1.5px,color:#111
    class SUP,SPECS,EV,RP center
    class SM,TEL side
    class GATE gate
    class TR,H terminus
```

**Reading the diagram.** The vertical sequence — trigger, Supervisor, Specialists, Evaluator, gate, Review Packet, Human — is the conceptual flow of one review cycle. System Mapper sits perpendicular because it's a one-shot topology lookup that feeds the Supervisor, not a stage in the recommendation pipeline. MCP scenario data sits perpendicular on the other side because it's the data source the Specialists (and Mapper) query via tool calls.

**Supervisor is the only router.** Although the diagram draws the sequence as a vertical chain, the implementation routes every transition through the Supervisor: Supervisor decides whether to call System Mapper, which specialists to dispatch, when to synthesize via the Evaluator, when to send the recommendation to the gate, and crucially — when to terminate the cycle. Every worker node returns to the Supervisor between stages; no worker can decide "we're done" on its own. The downward arrows are the conceptual sequence; the routing loop through the Supervisor is left implicit to keep the diagram readable.

Every arrow crosses one or more harnesses — the next section describes them. Note that this is a logical topology, not a microservice deployment diagram. For this portfolio implementation, the system runs as a single Python process; the architectural boundaries are strictly logical, not infrastructural.

## The Four Harnesses

The harnesses are not a fifth agent. They are system-wide constraints enforced across the agents themselves and the data they read and produce.

```mermaid
flowchart TB
    %% Node Definitions
    IH[Input Harness<br/>Validates scenario data & triggers]
    RH["Reasoning Harness<br/>Enforces structured output, evidence binding & scoring"]
    AH["Action Harness<br/>Scopes tool access & gates recommendations"]
    PAR["Persistent Action Record<br/>Append-only audit trail across all agents"]

    %% Main Execution Flow
    IH -->|applies at ingest phase| RH
    RH -->|applies to every specialist and evaluator turn| AH
    AH -->|applies at every tool call and final recommendation gate| PAR


    %% Audit Logging Flow
    IH -.->|"Logs"| PAR
    RH -.->|"Logs"| PAR
```
The Persistent Action Record captures state at every transition, so the full reasoning chain — from trigger to final synthesis — can be reconstructed from the audit trail.

## End-to-end Flow

```mermaid
flowchart TB
    T["Trigger<br/>Review request: app-name + optional alert description"]
    IH["Input Harness<br/>Validates scenario data & logs scenario hash"]

    SM["System Mapper<br/>Parses Terraform & generates analysis plan"]
    SUP["Supervisor Agent<br/>Orchestrates & routes to required specialists"]

    subgraph Specialists ["Parallel Specialist Analysis"]
        direction TB
        CA["Compute Analyst<br/>Executes ReAct in compute scope"]
        DA["Data Layer Analyst<br/>Executes ReAct in data layer scope"]
        NA["Network Analyst<br/>Executes ReAct in network scope"]
    end

    EV["Cross-Tier Evaluator<br/>Reconciles drift, maps interactions & scores trade-offs"]
    AH["Action Harness<br/>Gates recommendation (validates evidence, severity & duplication)"]
    HITL["Human-in-the-Loop (HITL)<br/>Approves, rejects, or defers"]
  
    %% Database node shape for the PAR
    PAR[("Persistent Action Record<br/>Append-only log across all stages")]

    %% Main Execution Flow
    T --> IH --> SM --> SUP
    SUP --> CA & DA & NA --> EV
    EV --> AH --> HITL

    %% Audit Logging Flow
    IH -.-> PAR
    SM -.-> PAR
    SUP -.-> PAR
    CA -.-> PAR
    DA -.-> PAR
    NA -.-> PAR
    EV -.-> PAR
    AH -.-> PAR
    HITL -.-> PAR
```
A review initiates with a lightweight trigger naming the target app and an optional alert description. The Supervisor pulls the initial package to plan the review, then specialists pull their tier's telemetry through the MCP surface as they reason. The Persistent Action Record captures state at every transition. The full reasoning chain, from trigger to final synthesis, can be reconstructed from the audit trail. Note that this diagram describes the conceptual reasoning structure, not a strict state-machine specification. Frameworks like LangGraph handle the actual control flow and state transitions underneath.

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

| Execution Stage | Input Harness | Reasoning Harness | Action Harness | Persistent Action Record |
| --- | --- | --- | --- | --- |
| **Trigger & Ingest** | Validates scenario data: schema, completeness, timestamp continuity | - | - | Logs trigger & scenario hash |
| **System Mapper** | Validates Terraform parsing | Enforces architecture model schema | - | Logs architecture model & analysis plan |
| **Supervisor Decisions** | - | - | - | Logs routing & invocation decisions |
| **Tier Specialist ReAct** | - | Enforces structured reasoning, evidence binding & confidence scoring | Scopes the MCP read surface to the specialist's tier | Logs every tool call & reasoning step |
| **Cross-Tier Evaluator** | - | Enforces drift-check, synthesis & trade-off scoring | - | Logs drift verdicts, scores & synthesis |
| **Final Recommendation Gate** | - | - | Gates well-formedness, evidence, severity & duplication | Logs final gate verdict |
| **HITL Decision** | - | - | - | Logs human approval, rejection, or deferral |

The Reasoning Harness carries the heaviest cognitive load. The Action Harness remains intentionally narrow. The Persistent Action Record maintains state across the entire lifecycle. The Input Harness acts strictly as the front door.
