# Audit Trail

The audit trail is the system's primary observability mechanism. It is treated as its own architectural component, not logging bolted onto each agent, because it is fundamentally different code from the agents themselves. Structured persistent storage with strong schema discipline, not agentic orchestration.

This doc covers what the audit trail captures, how it supports replay, why the storage choice is relational rather than vector, and what the schema looks like.

## What the audit trail captures

Every significant event in a review cycle is a record. The trail is rich enough that a reviewer reading it should be able to reconstruct exactly what the system did and why.

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

This discipline matters because the alternative (mutable records) makes the audit trail unreliable for governance purposes. A governance reviewer asking "what did the system recommend three months ago?"


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

Two append-only SQLite tables in a single file. The split is deliberate: the first table is the artifact a governance reviewer reads (the reasoning trail); the second is for developer-facing debugging of post-hoc operations (eval runs, report renders). Mixing them would dilute the main story.

### `audit_records` — the reasoning trail

One polymorphic table. Every event inside a review cycle is a row. The type field discriminates; the category field tells the two reports (decision trace, evidence trace) which records to walk.

```text
audit_records
  id                INTEGER PRIMARY KEY AUTOINCREMENT
  review_cycle_id   TEXT NOT NULL        -- e.g. "cycle_20260601_141522_a3f8b1c0"
  parent_id         INTEGER              -- self-FK; NULL only for the cycle root
  category          TEXT NOT NULL        -- 'decision' | 'evidence'
  type              TEXT NOT NULL        -- concrete sub-type (taxonomy below)
  agent             TEXT                 -- which agent or harness emitted it
  content           JSON NOT NULL        -- type-shaped payload
  emitted_at        DATETIME DEFAULT CURRENT_TIMESTAMP
  FOREIGN KEY (parent_id) REFERENCES audit_records(id)
  CHECK (category IN ('decision', 'evidence'))
```

Indexes:

- `UNIQUE INDEX one_start_per_cycle ON audit_records(review_cycle_id) WHERE type = 'cycle_started'` — the DB itself enforces cycle_id uniqueness.
- `UNIQUE INDEX one_end_per_cycle ON audit_records(review_cycle_id) WHERE type = 'cycle_completed'` — and one completion per cycle.
- `INDEX cycle_lookup ON audit_records(review_cycle_id, id)` — covers "all events for cycle X" queries.
- `INDEX parent_walk ON audit_records(parent_id)` — supports the recursive CTE.
- `INDEX category_type ON audit_records(category, type)` — supports the two reports.

### Cycle lifecycle modeled as events

There is no separate "reviews" table. A cycle exists when its `cycle_started` row exists; it is complete when a `cycle_completed` row is appended. The `cycle_completed` row's `parent_id` points back to `cycle_started`. Duration is computed at read time as `cycle_completed.emitted_at - cycle_started.emitted_at`.

This preserves strict append-only discipline: nothing is ever UPDATEd. A re-run is a new cycle (new `cycle_id`, new `cycle_started` row). Historic cycles are immutable. Cycle status is a query, not a state — derived from the existence of `cycle_completed`.

### Record taxonomy

**Decision-category** types (the chain of choices the system made — the spine of Report 1):

- `cycle_started`, `cycle_completed` — the begin and end tags
- `review_request` — the ingest trigger
- `supervisor_decision` — which specialists were invoked, retries, escalations
- `thought` — an agent's reasoning step inside a ReAct loop
- `specialist_finding` — a tier specialist's verdict
- `evaluator_record` — the cross-tier evaluator's synthesis
- `recommendation` — the final composite emitted by the cycle
- `gate_verdict` — Action Harness pass/fail (emitted in a future phase)
- `hitl_decision` — human approve/reject/defer (future)

**Evidence-category** types (the observed facts decisions cite — the leaves of Report 2):

- `tool_call` — an MCP call (parameters echoed)
- `observation` — the tool result, the actual data the agent saw
- `correlation_observation` — a specific correlation record cited
- `infrastructure_fact` — a specific configuration or terraform finding cited

A decision record's `content.evidence_refs: list[int]` carries the ids of evidence records it cites — this is the many-to-many citation mechanism. Forward citation queries use SQLite's `json_each(content, '$.evidence_refs')` to expand the array into rows and join cleanly; this avoids the `LIKE '%id%'` substring trap (which mis-matches id 5 against ids 15, 25, 50, etc.).

### `internal_ops` — operations on a completed cycle

Separate table, separate audience. Where `audit_records` is the deliverable, `internal_ops` is for developers debugging the system — eval runs, report renders, and similar post-hoc operations land here so the main trail stays focused on the reasoning story. Same DB file; same append-only discipline.

```text
internal_ops
  id                INTEGER PRIMARY KEY AUTOINCREMENT
  op_id             TEXT NOT NULL        -- e.g. "eval_20260601_142003_a3f8b1c0"
  op_type           TEXT NOT NULL        -- 'evaluation' | 'report_render' | 'evidence_render'
  target_cycle_id   TEXT NOT NULL        -- the audit_records cycle being operated on
  target_record_id  INTEGER              -- the specific record (typically the recommendation)
  parent_id         INTEGER              -- self-FK for multi-step ops
  type              TEXT NOT NULL        -- sub-type within the op
  content           JSON NOT NULL
  emitted_at        DATETIME DEFAULT CURRENT_TIMESTAMP
  FOREIGN KEY (parent_id) REFERENCES internal_ops(id)
```

Each evaluation produces a small chain:

- `judge_call` — the prompt sent to the LLM judge (evidence within the op)
- `evaluator_score` — the synthesized ScoreOneResult with all five layer verdicts (decision within the op)

Multiple evaluations against the same recommendation are supported (prompt tuning, judge non-determinism). Each invocation gets its own `op_id`; reports filter by `target_cycle_id` and group by `op_id`. Cross-table references use plain TEXT — `target_cycle_id` is not a database-level FK, both for SQLite simplicity and because the writer (`AuditStore`) controls both tables.

### JSON content payloads

Both tables store payloads in a `content JSON` column. The Pydantic content models in `src/models/audit.py` define the per-type shape (one class per record `type`). Pydantic validates the shape at write time; at read time, queries either inspect raw JSON via SQLite's `json_extract`/`json_each` or hydrate back into the typed model for application-level use. This is pragmatic for SQLite and migrates cleanly to Postgres `jsonb` or fully-typed columns later.

### Storage engine and file location

SQLite for the portfolio. The database file location is configured via the `AUDIT_DB_PATH` environment variable, defaulting to `.audit_db/audit.db` (hidden directory under the project root, gitignored — same pattern as the `.hf_cache/` location used for the published dataset). For Docker deployments, mount a volume to a known path and override `AUDIT_DB_PATH` in the container environment.

`PRAGMA foreign_keys = ON;` is set on every connection. SQLite does not enforce foreign keys by default, and the "every parent reference resolves" claim is empty without it. The `AuditStore` connection layer enforces this on every connect.

## What the audit trail does not do

**It is not an event bus** Agents do not communicate by writing to the audit trail and reading each other's writes. Agent-to-agent communication is the Supervisor's responsibility. The audit trail is a parallel write-only stream.

**It is not a knowledge base** Agents do not query the audit trail as part of their reasoning. They reason against the scenario data they pull via MCP. The audit trail exists for human-facing observability and replay, not for agent memory.

**It is not a metrics store** Operational metrics (latency, error rates, LLM token counts) may flow into a separate observability stack. The audit trail is for governance-facing reasoning traceability, which is a different artifact

Conflating any of these with the audit trail's purpose would dilute it. Keeping its scope clean is part of the architectural signal.

## The audit trail as the README's visual hero

The strongest single section of the README is the **audit-trail walkthrough**: pick one scenario, run it through the system, and show every record that was written, in causal order, with the recommendation at the end traceable back through the entire chain. A hiring manager who reads only that section should understand what the project does and why it is worth the engineering depth.

This is what "the audit trail is the artifact a governance reviewer engages with" means. The walkthrough is not a debugging tool. It is the system's primary deliverable to anyone who needs to **understand** a recommendation rather than just consume it.
