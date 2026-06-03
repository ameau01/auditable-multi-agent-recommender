# Harnesses

The four harnesses provide **structure, safety, and observability** across the entire agent system (see [ARCHITECTURE.md](ARCHITECTURE.md) for the high-level philosophy).

This document details what each harness provides, where it applies, and why it has its current shape.

## The four harnesses at a glance

| Harness                  | Core Capability                                                                    | Where it applies                                           |
| ------------------------ | ---------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Input Harness            | Schema validation and review trigger verification.                                 | Bundle ingestion (prior to any agent reasoning).           |
| Reasoning Harness        | Structured evidence-binding, two-level confidence scoring, and trade-off analysis. | During all specialist ReAct cycles and Evaluator turns.    |
| Action Harness           | Read-only tool surface enforcement and final recommendation gating.                | Wrapping every tool execution and final output generation. |
| Persistent Action Record | Append-only audit trail enabling replayable reasoning chains.                      | System-wide (written to continuously across all stages).   |

The harnesses are layered, not nested. A single agent invocation may interact with all four: the Input Harness validated its inputs, the Reasoning Harness shapes its reasoning and output, the Action Harness gates its tool calls, and the Persistent Action Record logs every step.

## 1. Input Harness

**What it provides.** Validation that the bundle (telemetry + sidecar) and the review trigger are well-formed and complete enough to support downstream analysis. Garbage in is stopped here so no specialist wastes a reasoning cycle on bad data.

**What it validates**

- **Schema conformance.** Telemetry tier arrays conform to the dataset schemas. Sidecar conforms to its schema.
- **Record completeness.** Each non-empty tier array has the expected 1,344 records.
- **Timestamp continuity.** Timestamps are monotonic, 15 minutes apart, no gaps or duplicates.
- **Cross-tier alignment.** Timestamps align index-by-index across the tier arrays used in this bundle.
- **Sidecar field presence.** Sidecar fields referenced by the read contract (top_queries for query scenarios, per_instance_breakdown for load-balancer scenarios) are present and non-empty.
- **Trigger legitimacy.** The review trigger is well-formed and identifies an application in scope.
- **Terraform parseability.** The application's Terraform definition parses without error. Validation of correctness is out of scope.

**Where it applies.** At the front door. Every review request passes through the Input Harness before the Supervisor invokes any other agent. Failures produce a clear rejection with the specific validation that failed, logged to the audit trail.

**Why this is its own harness.** Input validation may look unsophisticated next to reasoning or audit. It is included as a distinct harness because the alternative, letting downstream agents discover bad inputs through their own failures, produces useless audit trails ("specialist could not find evidence" when the real failure was that telemetry was incomplete). Validating at the front door means downstream audit entries are about real reasoning, not data quality artifacts.

**What it does not do.**

- Validate semantic correctness. A bundle that passes schema and completeness checks but contains unrealistic values still passes. Semantic plausibility is the dataset pipeline's job.
- Perform adversarial input filtering. There is no external user input in this system.

## 2. Reasoning Harness

The cognitive infrastructure that makes specialist and Evaluator reasoning credible. **The heaviest of the four harnesses.** Most of the system's intelligence lives in the structures it enforces.

### What it enforces

**Structured output with evidence-binding.** Every specialist finding and every Evaluator synthesis is emitted as a structured object with a fixed schema. Every recommendation field within the structure must reference the specific read operations (and their results) that justified it. A recommendation without evidence references is structurally rejected before it can be emitted.

The output schema for a specialist finding:

| Field                   | Purpose                                                                                                                |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `finding_type`          | One of `issue_found`, `no_issue_found`, `insufficient_data`. Explicitly three-valued to prevent action bias.           |
| `recommendation`        | The specific action recommended (populated only when `finding_type = issue_found`).                                    |
| `evidence_refs`         | List of read-operation results that justified the recommendation. Required non-empty when a recommendation is present. |
| `reasoning_trace`       | The ReAct hypothesize / act / observe trace that led to the finding.                                                   |
| `specialist_confidence` | A scalar combining evidence sufficiency and within-tier pattern strength.                                              |
| `confidence_breakdown`  | The sub-signals contributing to the confidence score, named individually.                                              |

The Evaluator's output schema extends this with cross-tier interactions, trade-off scores, drift-check verdicts per specialist, and evaluator-level confidence.

**Evidence sufficiency threshold.** A `recommendation` field can only be populated if `evidence_refs` exceeds a minimum count and meets the specialist's evidence threshold for its domain. Falling below either threshold forces the specialist to emit `no_issue_found` or `insufficient_data`.

**Two-level confidence scoring.** Specialists produce a specialist-level confidence. The Evaluator produces an evaluator-level confidence. The two levels measure different things and combine differently. Detail below.

**Trade-off analysis.** The Evaluator produces explicit trade-off scores across cost, performance, and reliability. Each is scored on its own scale rather than collapsed into a single composite. The trade-off is articulated in the reasoning trace so the human reviewer can see how dimensions were weighed.

### Where it applies

At every specialist ReAct turn (each thought → action → observation cycle is subject to the harness's structuring) and at every Evaluator step (drift-check, cross-tier identification, synthesis). The harness does not apply to the Supervisor or System Mapper, whose outputs follow simpler workflow-orchestration and parsing schemas respectively.

### Why this is the heaviest harness

The Reasoning Harness is where the architectural claim "this system reasons soundly" becomes a structural property rather than a hope.

- Without evidence-binding, recommendations are leaps.
- Without the three-valued `finding_type`, specialists invent problems.
- Without two-level confidence, the drift-check has nothing to gate on.
- Without trade-off scoring, the synthesis is "pick a winner" rather than "balance dimensions."

Most of what makes this project distinctive lives here. The specialists and the Evaluator are vehicles for the reasoning the harness enforces.

## 2a. Confidence and drift (Reasoning Harness subsystem)

The Reasoning Harness produces two levels of confidence and the Cross-Tier Evaluator performs a drift-check on each specialist's reasoning before synthesizing. Together, these are the system's defense against single-specialist hallucination.

### Two levels of confidence

A single composite confidence score conflates two genuinely different concerns. A specialist working within its own tier can measure how strong its evidence is and how clear its pattern is, but cannot measure cross-tier consistency. The Evaluator can measure cross-tier consistency but should not re-derive specialist-level confidence, that would defeat the point of having specialists.

The system therefore produces **two confidence scores at different layers, measuring different things.**

**Specialist-level confidence.** Produced by each Tier Specialist as part of its structured finding. Combines two signals, both measurable within the specialist's own tier:

- **Evidence sufficiency.** How many evidence references support the conclusion, and how well they cover the relevant time windows. A finding citing one tool-call observation is weaker than one citing four observations from independent angles. The Reasoning Harness enforces a minimum count before any `issue_found` can be emitted.
- **Within-tier pattern strength.** For findings that depend on a recurring pattern, daily spikes, business-hours load, sustained underutilization, how well the pattern holds. A pattern that holds on 11 of 14 days (the dataset's "11 of 14" rule) is unambiguous; one that holds on 7 of 14 is suggestive but not strong.

The specialist confidence is the product of these two signals, each normalized to a 0, 1 scale. The full breakdown is logged so a reviewer can see which sub-signal drove the score.

**Evaluator-level confidence.** Produced by the Cross-Tier Evaluator as part of the synthesized recommendation. Combines three signals:

- **Aggregated specialist confidence.** The average (or minimum, depending on policy) of the specialist-level confidences contributing to this synthesis. A synthesis built on three low-confidence findings cannot itself be high-confidence.
- **Cross-tier consistency.** For scenarios where multiple specialists found related issues, how well their findings align. If Compute implicates the database and Data Layer confirms a database bottleneck, consistency is high. If they contradict, consistency is low and the Evaluator must reason through the conflict before synthesizing.
- **Trade-off resolution clarity.** How decisively the cost / performance / reliability trade-off was resolvable from the evidence. A recommendation where all three dimensions point the same way (right-size: cheaper, no perf impact, no reliability impact) has high clarity. A recommendation where dimensions conflict (reduce capacity: cheaper, but reliability risk) has lower clarity, and the Evaluator must articulate the trade-off rather than collapse it.

Like specialist confidence, evaluator confidence is logged with its full breakdown.

### Why this split matters

The two-level design serves three purposes:

- **Honest scoping.** Specialists measure what specialists can measure. The Evaluator measures what only a synthesis layer can measure. Neither pretends to measure the other.
- **Drift-check input.** The Evaluator's drift-check needs to compare specialist-level confidence to its own assessment of specialist reasoning quality. If a specialist reports high confidence but the drift-check finds the reasoning weak, that's a signal.
- **HITL transparency.** The human reviewer sees both levels of confidence in the review packet, with the full breakdown. They can ask: "Was this synthesis confident because the specialists were confident, or because the cross-tier picture was unambiguous?" The answer is in the breakdown.

### The Evaluator's drift-check

For each specialist finding, the Evaluator performs an explicit drift-check before incorporating the finding into synthesis. The drift-check asks three questions:

**Q1. Does the recommendation follow from the cited evidence?**

The Evaluator examines each evidence reference and asks whether the cited observation actually supports the conclusion drawn. A specialist that cites "CPU p50 18, 22%" as evidence for "downsize compute by 50%" is making a justifiable inference. A specialist that cites the same evidence as justification for "add a read replica" is reaching beyond what the evidence supports.

**Q2. Is the evidence-binding tight, or does the recommendation reach beyond what the evidence supports?**

Some specialists may produce technically correct recommendations but with weak or partial evidence chains. The Evaluator distinguishes **well-evidenced** from **directionally correct but under-evidenced**. Both can be incorporated into synthesis, but the latter weights the recommendation less heavily and surfaces a flag in the review packet.

**Q3. Are there internal contradictions within the reasoning trace?**

A specialist whose ReAct trace shows "observe X, conclude Y", but X and Y are logically inconsistent, has drifted. This is the strongest drift signal. Contradictory specialists are flagged in the review packet and their findings are de-weighted in the synthesis.

Each drift-check produces a verdict per specialist: `tight`, `loose`, or `contradictory`. All three verdicts are logged to the audit trail.

### Why drift-check is first

The Evaluator runs drift-check **before** cross-tier mapping and synthesis so a weak or contradictory finding cannot pollute the final recommendation.

### Policy choices that need to be tuned during build

Two policy choices are intentionally tunable rather than hardcoded:

- **Aggregation policy for specialist confidence.** Should the Evaluator average specialist confidences, take the minimum, or weight by drift-check verdict? Each has a defensible rationale. The build phase tunes this against the eval set.
- **Confidence thresholds.** Below what confidence does the Supervisor retry a specialist? Below what evaluator confidence does the recommendation get flagged as low-confidence in the review packet? These are configuration, not constants, and will be tuned during build.

Both choices are called out as configuration to make them visible and tunable rather than hidden in code. Recognizing them as choices worth surfacing is itself part of the signal.

### What drift-check does not catch

The Evaluator's drift-check is a real defense against single-specialist drift. It is honestly limited in two ways:

- **Correlated multi-specialist drift.** When all three specialists drift in the same direction with no internal conflict between them, the Evaluator has no signal to flag. Three specialists that all over-recommend cost cuts will look consistent to the Evaluator because they are consistent, just consistently wrong. This class of failure is structurally caught by HITL review: the human, armed with the audit trail and their own judgment, may notice what the Evaluator could not.
- **Evaluator drift.** The Evaluator can hallucinate too. Its synthesis can drift away from what the specialist findings support. The mitigation is the audit trail: the Evaluator's synthesis cites which specialist findings supported it, and the human reviewer can spot Evaluator drift by walking the citation chain.
- **Evidence that is itself wrong.** The Reasoning Harness enforces evidence-binding, but it cannot verify the evidence itself is correct. The Input Harness mitigates this at ingest by validating that data is well-formed and complete, but semantic correctness of the data is the dataset generator's responsibility, not the consumer's.

Naming these limits honestly is part of the architectural signal. The system is not omniscient. What it does provide is a structure where failures, when they happen, are surfaceable and auditable. A reviewer concludes the recommendation is worth acting on from the chain they can walk, not from a property the system asserts about itself.

### How this shows up in the review packet

The human reviewer sees, for every recommendation:

- The **evaluator-level confidence** with its breakdown (aggregated specialist confidence, cross-tier consistency, trade-off resolution clarity).
- For each contributing specialist: the **specialist-level confidence** with its breakdown, and the Evaluator's **drift-check verdict**.
- The **full reasoning chain** that justifies the recommendation, with each evidence reference resolvable in the audit trail.

A reviewer skeptical of a recommendation can drill into the chain. A reviewer who trusts the synthesis can approve it quickly. Both behaviors are supported by the same data.

This is what "the audit trail is the visual hero" means in practice. The trail is not a debugging artifact; it is the substrate the human uses to make a decision.

## 3. Action Harness

**What it provides.** Two scoped jobs.

**(1) Tool-surface control during analysis.** Each agent can only invoke the read operations appropriate to its scope. Tier Specialists are limited to their own tier's read surface. Cross-tier operations are reserved for the Evaluator. The System Mapper has its own tool surface for Terraform parsing.

**(2) Recommendation quality gate before HITL.** The final synthesized recommendation, before being surfaced to the human reviewer, passes through a gate that checks:

- **Well-formedness** of the review packet (all required fields populated, evidence chain intact).
- **Evidence completeness** (the recommendation's evidence references actually resolve to logged read operations in the audit trail).
- **Severity classification** (recommendations classified low/medium/high for HITL routing, higher severity gets more prominent placement and stricter human-review expectations).
- **Duplication check** (a recommendation that exactly matches a recent prior recommendation gets flagged so the human sees the repetition).

Tool calls the harness allows land their `tool_call` + `observation` events in `audit_records`; the policy-check verdict and the recommendation-gate verdict land in `harness_trail`. A rejected tool call has no `audit_records` entry — the rejection lives only in `harness_trail`.

### Why intentionally narrow

In a system that actually modifies infrastructure, the Action Harness would be much larger (executable actions, API validation, rollback paths). None of that applies here. This system is purely a recommender under HITL, so the harness stays deliberately narrow.

Inflating the Action Harness to look bigger would dilute the system's identity. The discipline of keeping it small is part of the architectural signal. See `../ARCHITECTURE.md` section 1 and `decisions.md`.

### Where it applies

- Around every tool call by every agent (tool-surface control).
- At the final recommendation, between the Evaluator's synthesis and the HITL surfacing (recommendation quality gate).

### What it does not do

- Actuate state changes. The system is a recommender.
- Gate the Evaluator's synthesis content. The Reasoning Harness shapes that. The Action Harness only checks the resulting review packet for completeness and classification.
- Enforce role-based access control. Role-based filtering is out of scope for a system that produces recommendations for a single human reviewer per review.

## 4. Persistent Action Record

**What it provides.** An append-only audit trail across every agent and every decision, supporting deterministic replay of any recommendation back to its evidence chain.

Full schema and replayability story live in `audit-trail.md`. The summary here:

**What gets logged.** Every significant event in a review cycle becomes a record:

- Trigger events, review request, bundle hash, Input Harness validation outcomes.
- System Mapper events, architecture model produced, analysis plan generated.
- Supervisor decisions, which specialists invoked, why; low-confidence handling decisions; retries.
- Specialist ReAct steps, every thought, action, observation. Each tool call logged with its parameters and result.
- Specialist findings, `finding_type`, recommendation (if any), `evidence_refs`, `reasoning_trace`, confidence breakdown.
- Evaluator events, drift-check verdicts per specialist, cross-tier interactions identified, trade-off scores, synthesis output, evaluator confidence breakdown.
- Action Harness events, recommendation gate verdict, severity classification, duplication check result.
- HITL events, review packet surfaced, human decision (approve/reject/defer) with timestamp and any reviewer notes.

Every record carries a stable identifier, the agent or harness that emitted it, and foreign-key references to upstream records in the chain.

**Append-only by design.** Records are never updated or deleted. Corrections produce new records that reference the original. This is what makes "replayable" a real property rather than a hope.

**Storage.** SQLite for the portfolio implementation, with the schema designed so Postgres is a drop-in upgrade for production.

The audit trail spans three append-only tables in one SQLite file: `audit_records` for the agent's reasoning, `harness_trail` for what the harnesses verified or rejected, and `operations` for post-hoc operations like eval scoring. The split keeps each table focused on one audience; full schema lives in [`audit-trail.md`](audit-trail.md).

### Why this is its own harness

Treating the audit trail as its own architectural component, rather than logging bolted onto each agent, reflects how production systems are actually built. The audit trail is the artifact a compliance or optimization reviewer engages with. It deserves its own schema, its own write discipline, and its own correctness story.

It is also the artifact that turns "I built an agent system" into "I built an agent system that could pass a optimization review." The other three harnesses depend on this one for accountability, they enforce structure and quality, but the audit trail is what makes the enforcement verifiable after the fact.

### What it does not do

- It is not an event bus. Agents do not communicate through the audit trail; they communicate through the Supervisor's orchestration. The audit trail is a parallel write-only stream.
- It is not a knowledge base. Agents do not query the audit trail for past scenarios as input to current reasoning.
- It is not a vector store. The access patterns are foreign-key traversal and structured queries.

## How the harnesses interact

The harnesses are independent in scope but cooperative in practice. A representative interaction:

A Compute Analyst invokes a tool call to detect threshold breaches on `cpu_p95`. The Action Harness validates that this read operation is in the Compute Analyst's scope (it is). The tool call runs and returns results. The Reasoning Harness requires that any conclusion citing this tool call include the call as an `evidence_ref`. The Persistent Action Record logs the tool call's intent and result to `audit_records` (the substance the agent saw), the Action Harness's policy-check verdict to `harness_trail` (the enforcement record), and when the specialist concludes, the finding citing this evidence lands back in `audit_records`. Three writes across two tables, one tool call.

Four harnesses, one tool call, four properties enforced: scope (Action Harness), evidence-binding (Reasoning Harness), audit (Persistent Action Record), and, implicitly, the assurance that the tool call's inputs were valid (Input Harness validated them at bundle ingest).

This is what "structure, safety, and observability" means in practice. Not five defenses against five threats, but four properties enforced uniformly across the system.
