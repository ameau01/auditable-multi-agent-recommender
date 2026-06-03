# Audit Trail

The audit trail is the system's primary observability mechanism. It is treated as its own architectural component, not logging bolted onto each agent, because it is fundamentally different code from the agents themselves. Structured persistent storage with strong schema discipline, not agentic orchestration.

This doc covers what the audit trail captures, how it supports replay, why the storage choice is relational rather than vector, and what the schema looks like.

## What the audit trail captures

Every significant event in a review cycle is a record. The trail is rich enough that a reviewer reading it should be able to reconstruct exactly what the system did and why.

These records split across three tables. The reasoning events (Supervisor decisions, ReAct steps, specialist findings, evaluator records, recommendation, HITL decisions) land in `audit_records` — the agent's story for the human reviewer. The enforcement events (Input Harness validations, Action Harness policy checks and gate verdicts, Reasoning Harness checks) land in `harness_trail`. Post-hoc scoring runs against a completed cycle's recommendation land in `operations`. See "Storage shape" below for the per-table schemas.

**Trigger and ingest records** When a review was requested, by what trigger, against which application, with what scenario hash.

**System Mapper records** The architecture model produced for this review (tiers, dependencies, analysis plan). Any parsing diagnostics from Terraform.

**Supervisor decision records** Which specialists were invoked and why. Low-confidence handling decisions. Retry decisions. Escalations.

**Specialist ReAct step records** For each specialist, every cycle of the ReAct loop: the thought, the action (tool call with parameters), the observation (tool result). Each step is its own record, ordered.

**Specialist finding records** The structured output from each specialist: `finding_type`, recommendation (if any), `evidence_refs`, `reasoning_trace` summary, specialist confidence with breakdown.

**Evaluator records** Drift-check verdicts per specialist. Cross-tier interactions identified. Trade-off scores across cost / performance / reliability. Final synthesized recommendation. Evaluator confidence with breakdown.

**Action Harness records** The recommendation gate verdict. The severity classification. The duplication check result.

**Review packet records** The full review packet as surfaced to the human reviewer. Stored as the canonical artifact, not just a pointer to it.

**HITL records** The human's decision (approve / reject / defer), timestamp, and any notes the reviewer attached.

Every record carries:

- A stable unique identifier.
- A timestamp.
- The agent or harness that emitted it.
- The review-cycle identifier (so all records from one cycle can be retrieved together).
- Foreign-key references to upstream records in the causal chain.

The foreign keys are what make the trail navigable: from a final HITL approval, the reader can walk backward through Action Harness gate → review packet → Evaluator synthesis → drift-check verdicts → specialist findings → specialist ReAct steps → tool calls → ingest records.

## Append-only by design

Records are never updated or deleted. This is the property that makes "replayable" real rather than aspirational.

If a recommendation needs to be corrected, for example, the human reviewer rejects it and provides feedback, the correction is a **new record** that references the original. The historical state at any past point in time is exactly the records that existed up to that point. Nothing is rewritten retroactively.

This discipline matters because the alternative (mutable records) makes the audit trail unreliable for accountability. A reviewer asking "what did the system recommend three months ago?" must get the same answer today, tomorrow, and a year from now.

In storage terms, append-only is enforced both by schema discipline (no `UPDATE` paths in the data access layer) and by application discipline (corrections produce new records). For the portfolio in SQLite, this is straightforward; for a production deployment in Postgres, the same pattern applies.

## Replayability

A review is **replayable** if, given the review-cycle identifier, the full reasoning chain can be reconstructed from the audit trail by following foreign-key references in either direction.

What this means in practice:

- Every recommendation has explicit citations to the Evaluator records that produced it.
- Every Evaluator record has explicit citations to the specialist findings that informed it (via `contributing_findings`).
- Every drift-check verdict names the specific finding it judged (via `target_finding_id`).
- Every specialist finding has explicit citations to the ReAct observations it relied on (via `evidence_refs`).
- Every ReAct step has an `observation_id` so the finding's citations resolve to lookable records.
- Every tool call records the scenario hash and exact parameters used.

The `harness_trail` provides a parallel enforcement chain: for any tool call in `audit_records`, the corresponding policy-check record in `harness_trail` tells you whether the call was permitted and why. A rejected tool call has only a `harness_trail` entry — its absence from `audit_records` is itself the audit signal that "this was attempted but not allowed."

A developer or reviewer can walk the chain forward or backward. The system's reasoning is not a black box; it is a navigable graph.

**What replayability does NOT mean** Replay reconstructs what happened from the stored records. It does not re-run the LLM. LLM outputs are non-deterministic, so the audit trail captures the actual outputs that occurred.

### What's verifiable today

The full agent system is not yet implemented (Phase 7 in `CHANGELOG.md`). Until it is, the auditability contract is verified by:

- **`tests/verify_trace.py`** A standalone script that walks the sample traces backward and confirms every parent reference resolves. Runs in under a second; exits non-zero if any pointer is dangling.
- **`sample_runs/traces/scenario_NN_trace.json`** Sample traces for scenarios 02, 07, and 08 with all the required IDs and foreign keys in place.

When the agents are implemented, the Action Harness's `evidence_completeness` check runs the same verification at gate time on every live review. The `verified_refs` field on that check is what carries the result.

## Why relational, not vector

A vector database was considered for the audit trail and rejected. The reasoning matters for the design-decisions narrative, see `decisions.md` for the long-form version. Briefly:

**The audit trail's access patterns are relational**

- Append-only writes of structured records.
- Foreign-key traversal to reconstruct causal chains.
- Structured queries ("show all recommendations rejected by HITL in the last 30 days," "show all reviews where drift-check flagged a specialist").
- Deterministic replay from a known starting point.

None of these are similarity-search patterns. A vector database would turn deterministic replay into approximate similarity, drop the foreign-key guarantees, and provide nothing the audit trail needs that relational storage does not already give.

A vector database **would** be appropriate for a different concern, semantic retrieval over past reasoning traces, where an agent wants to find scenarios similar to the current one. That is a separate concern (agent memory), not currently in scope, and it would be a parallel storage system, not a replacement for the relational audit trail.

**Storage engine** SQLite for the portfolio. Single file, zero infrastructure, ships with Python. The audit trail demo runs anywhere. The senior signal is the schema design, not the engine, the same schema upgrades cleanly to Postgres for production.

## Storage shape

Three append-only SQLite tables in a single file. Each has a distinct audience:

- `audit_records` — the agent's reasoning story, for the human reviewer
- `harness_trail` — what the harnesses verified or rejected, for harness reporting
- `operations` — post-hoc operations on completed cycles, for developers debugging

Splitting them keeps each table focused on one story for one audience. Mixing them would dilute the reasoning trail with enforcement noise and operational logs.

### `audit_records` — the reasoning trail

One polymorphic table. Every event inside a review cycle is a row. The `type` field discriminates the concrete event; `category` partitions into decision events (the spine of the trail) and evidence events (the observations decisions cite).

```text
audit_records
  id                INTEGER PRIMARY KEY AUTOINCREMENT
  cycle_id          TEXT NOT NULL        -- e.g. "cycle_20260601_141522_a3f8b1c0"
  parent_id         INTEGER              -- self-FK; NULL only for the cycle root
  category          TEXT NOT NULL        -- 'decision' | 'evidence'
  type              TEXT NOT NULL        -- concrete sub-type (taxonomy below)
  agent             TEXT                 -- which agent or harness emitted it
  content           JSON NOT NULL        -- type-shaped payload
  timestamp         DATETIME DEFAULT CURRENT_TIMESTAMP
  FOREIGN KEY (parent_id) REFERENCES audit_records(id)
  CHECK (category IN ('decision', 'evidence'))
```

Indexes:

- `UNIQUE INDEX one_start_per_cycle ON audit_records(cycle_id) WHERE type = 'cycle_started'` — the DB itself enforces cycle_id uniqueness.
- `UNIQUE INDEX one_end_per_cycle ON audit_records(cycle_id) WHERE type = 'cycle_completed'` — and one completion per cycle.
- `INDEX cycle_lookup ON audit_records(cycle_id, id)` — covers "all events for cycle X" queries.
- `INDEX parent_walk ON audit_records(parent_id)` — supports the recursive CTE.
- `INDEX category_type ON audit_records(category, type)` — supports decision-vs-evidence filtering.

### Cycle lifecycle modeled as events

There is no separate "reviews" table. A cycle exists when its `cycle_started` row exists; it is complete when a `cycle_completed` row is appended. The `cycle_completed` row's `parent_id` points back to `cycle_started`. Duration is computed at read time as `cycle_completed.timestamp - cycle_started.timestamp`.

This preserves strict append-only discipline: nothing is ever UPDATEd. A re-run is a new cycle (new `cycle_id`, new `cycle_started` row). Historic cycles are immutable. Cycle status is a query, not a state — derived from the existence of `cycle_completed`.

### Canonical renderer traversal

`audit_records` carries three relationships, and the renderer treats them as having different jobs. The rule below is the one canonical traversal — both the decision report and the evidence report derive from it, so readers and renderers don't have to negotiate between `parent_id`, `evidence_refs`, and `related_event_id` on the fly.

**Spine (chronological by `id`).** The decision report is the rows of a cycle in ascending `id` order, filtered to `category = 'decision'`. `id` is monotonically increasing within a cycle (single-writer append), so reading by `id` reconstructs the order events were produced. No tree walk is needed for the spine.

**Citations (`evidence_refs`).** Every decision row carries a `content.evidence_refs: list[int]` of `audit_records.id` values it was derived from. The renderer resolves these on demand to attach the cited observations to the decision in the evidence report. The Reasoning Harness's `decision_evidence_backed` check verifies at write time that every cited id exists in the same cycle, so dangling and foreign references never reach the renderer.

**`parent_id` is scoped, on purpose.** It is used in two cases only:

  1. Evidence chains within the same agent step — `observation.parent_id → tool_call.id` lets a reader see which observation came from which call without scanning by timestamp.
  2. Cycle close — `cycle_completed.parent_id → cycle_started.id` brackets the cycle so the start/end pair is structurally linked.

`parent_id` is **not** used to chain decisions to the prior decisions that motivated them; that's what `evidence_refs` is for. Mixing the two would force the renderer to decide which ancestry to trust and would conflate "produced by" with "cites as evidence."

**`related_event_id`** lives on `harness_trail`, not `audit_records`. It denormalizes the audit row a harness check was about, so harness reporting doesn't need an `audit_records` join. The renderer doesn't read it for the decision or evidence reports.

So: the decision report is `audit_records WHERE cycle_id = ? AND category = 'decision' ORDER BY id`. The evidence report is the same spine, with each row's `evidence_refs` resolved to the cited rows. No ad-hoc traversal, no per-row decision about which relationship to follow.

### `harness_trail` — what the harnesses verified or rejected

The second table records *enforcement events*: validations from the Input Harness, policy checks and gate verdicts from the Action Harness, and pre-emit checks from the Reasoning Harness. Kept separate from `audit_records` so the agent's reasoning story stays focused on substance — what the agent *did* — while `harness_trail` tells the parallel story of what was *verified or prevented*.

```text
harness_trail
  id                INTEGER PRIMARY KEY AUTOINCREMENT
  cycle_id          TEXT NOT NULL
  parent_id         INTEGER              -- self-FK; chains of harness checks
  related_event_id  INTEGER              -- denormalized ref to audit_records.id
  harness           TEXT NOT NULL        -- 'input' | 'action' | 'reasoning'
  type              TEXT NOT NULL
  verdict           TEXT NOT NULL        -- 'passed' | 'rejected' | 'flagged' | 'info'
  content           JSON NOT NULL
  timestamp         DATETIME DEFAULT CURRENT_TIMESTAMP
  FOREIGN KEY (parent_id) REFERENCES harness_trail(id)
```

The architectural property worth seeing: when the Action Harness allows a tool call, the resulting tool call and observation land in `audit_records` (the substance). The harness's policy-check verdict lands in `harness_trail` (the enforcement decision). When the Action Harness *rejects* a tool call, there is no `audit_records` entry — the rejection lives only in `harness_trail`. The audit trail shows what the agent accomplished; the harness trail shows what was prevented.

### `operations` — operations on a completed cycle

Third table, third audience. Where `audit_records` is the deliverable and `harness_trail` is the enforcement record, `operations` is for developers debugging the system — eval runs, report renders, and similar post-hoc operations land here so the main trail stays focused on the reasoning story. Same DB file; same append-only discipline.

```text
operations
  id                INTEGER PRIMARY KEY AUTOINCREMENT
  op_id             TEXT NOT NULL        -- e.g. "eval_20260601_142003_a3f8b1c0"
  op_type           TEXT NOT NULL        -- 'evaluation' | 'report_render' | 'evidence_render'
  target_cycle_id   TEXT NOT NULL        -- the audit_records cycle being operated on
  target_record_id  INTEGER              -- the specific record (typically the recommendation)
  parent_id         INTEGER              -- self-FK for multi-step ops
  type              TEXT NOT NULL        -- sub-type within the op
  content           JSON NOT NULL
  timestamp         DATETIME DEFAULT CURRENT_TIMESTAMP
  FOREIGN KEY (parent_id) REFERENCES operations(id)
```

Each evaluation produces a small chain:

- `judge_call` — the prompt sent to the LLM judge (evidence within the op)
- `evaluator_score` — the synthesized ScoreOneResult with all five layer verdicts (decision within the op)

Multiple evaluations against the same recommendation are supported (prompt tuning, judge non-determinism). Each invocation gets its own `op_id`; reports filter by `target_cycle_id` and group by `op_id`. Cross-table references use plain TEXT — `target_cycle_id` is not a database-level FK, both for SQLite simplicity and because the writer (`AuditStore`) controls both tables.

### JSON content payloads

All three tables store payloads in a `content JSON` column. The Pydantic content models in `src/models/audit.py` define the per-type shape (one class per record `type`). Pydantic validates the shape at write time; at read time, queries either inspect raw JSON via SQLite's `json_extract`/`json_each` or hydrate back into the typed model for application-level use. This is pragmatic for SQLite and migrates cleanly to Postgres `jsonb` or fully-typed columns later.

### Storage engine and file location

SQLite for the portfolio. The database file location is configured via the `AUDIT_DB_PATH` environment variable, defaulting to `.audit_db/audit.db` (hidden directory under the project root, gitignored — same pattern as the `.hf_cache/` location used for the published dataset). For Docker deployments, mount a volume to a known path and override `AUDIT_DB_PATH` in the container environment.

`PRAGMA foreign_keys = ON;` is set on every connection. SQLite does not enforce foreign keys by default, and the "every parent reference resolves" claim is empty without it. The `AuditStore` connection layer enforces this on every connect.

## What the audit trail does not do

**It is not an event bus** Agents do not communicate by writing to the audit trail and reading each other's writes. Agent-to-agent communication is the Supervisor's responsibility. The audit trail is a parallel write-only stream.

**It is not a knowledge base** Agents do not query the audit trail as part of their reasoning. They reason against the scenario data they pull via MCP. The audit trail exists for human-facing observability and replay, not for agent memory.

**It is not a metrics store** Operational metrics (latency, error rates, LLM token counts) may flow into a separate observability stack. The audit trail is for human-facing reasoning traceability, which is a different artifact.

Conflating any of these with the audit trail's purpose would dilute it. Keeping its scope clean is part of the architectural signal.

## The audit trail as the README's visual hero

The strongest single section of the README is the **audit-trail walkthrough**: pick one scenario, run it through the system, and show every record that was written, in causal order, with the recommendation at the end traceable back through the entire chain. A hiring manager who reads only that section should understand what the project does and why it is worth the engineering depth.

This is what "the audit trail is the artifact a reviewer engages with" means. The walkthrough is not a debugging tool. It is the system's primary deliverable to anyone who needs to **understand** a recommendation rather than just consume it.
