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
2. **Multi-agent by necessity** Earned, not decorative. Each agent owns a strictly bounded scope. Specialists analyze deeper because their read surface is narrow. If a single agent tries to process compute, network, and database telemetry all at once, it loses focus and outputs shallow analysis. The hierarchical network (Supervisor, Specialists, Evaluator) structurally enforces these strict boundaries.
3. **Accountability over adversarial defense** Because the system takes no external user input, prompt injection is not a meaningful threat. Our failure modes focus entirely on reasoning quality, consistency, and auditability.
4. **Deliberate synthetic data** Establishing strict ground truth requires hand-crafted scenarios. The dataset is published at [`ameau01/synthesized-cloud-optimization-recommendations`](https://huggingface.co/datasets/ameau01/synthesized-cloud-optimization-recommendations) on Hugging Face.
5. **Harnesses provide properties, not defenses** Harness layers are designed to enforce structure, safety, and observability. They are not a checklist of security defenses mapped against hypothetical threats.
6. **Model specialization over scale** We use Haiku for the high-volume, bounded specialist turns, and Sonnet for the single, complex Evaluator synthesis. Cost and capability are matched exactly to the workload.
7. **Trade-offs are part of the deliverable** Every architectural decision has rejected alternatives. That reasoning is explicitly tracked in [docs/decisions.md](docs/decisions.md).

The deeper rationale for each principle is in [docs/decisions.md](docs/decisions.md) and the sections below.

## System Overview

```mermaid
flowchart TB
    subgraph Trigger["Review trigger"]
        TR[Review trigger<br/>app-name + optional alert description]
    end

    subgraph Scenario["Scenario data — validated by the Input Harness, pulled via MCP"]
        TF[Terraform definition]
        TEL[Telemetry: 14 days, 15-min intervals]
        SC[Sidecar metadata]
    end

    subgraph Pipeline["Agent pipeline"]
        SUP[Supervisor<br/>workflow orchestration]
        SM[System Mapper<br/>Terraform -> tier graph]
        CA[Compute Analyst<br/>ReAct, tier-bounded]
        DA[Data Layer Analyst<br/>ReAct, tier-bounded]
        NA[Network Analyst<br/>ReAct, tier-bounded]
        EV[Cross-Tier Evaluator<br/>drift-check + synthesis]
    end

    subgraph Output
        RP[Review Packet<br/>recommendation + evidence chain]
        H[Human reviewer<br/>approve / reject / defer]
    end

    TR --> SUP
    SUP --> SM
    SM -.->|pull initial package via MCP| Scenario
    SM --> SUP
    SUP --> CA
    SUP --> DA
    SUP --> NA
    CA -.->|pull tier telemetry via MCP| Scenario
    DA -.->|pull tier telemetry via MCP| Scenario
    NA -.->|pull tier telemetry via MCP| Scenario
    CA --> EV
    DA --> EV
    NA --> EV
    EV --> RP
    RP --> H
```

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

**Execution Boundaries:** Each specialist operates within strict structural constraints, utilizing a mandatory ReAct loop to generate the audit trail and a three-state terminal output to prevent action bias. The full mechanical breakdown of these specialists are terminal states are detailed in [`docs/agents.md (Tier Specialists)`](docs/agents.md).

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

**Evaluator Logic:** The Evaluator follows a strict three-step sequence—enforcing drift-checks *before* mapping cross-tier interactions—to prevent hallucinated findings from polluting the final synthesis. The specific mechanics of this sequence, and the edge cases it explicitly delegates to human-in-the-loop (HITL), are detailed in [docs/agents.md (Cross-Tier Evaluator)](docs/agents.md).


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
