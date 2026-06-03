"""Read-only helpers for the audit store.

All functions take an `AuditStore` and return typed Pydantic models —
never raw rows. They never write. The write path lives in
`store.py`.

Key reads:

  - `get_decision_chain(start_record_id)` — recursive CTE walks parent_id
    backward over `audit_records`, returning the chain of decision-category
    records that led to `start_record_id`. Powers the key-decision report.
  - `get_evidence_consumers(evidence_record_id)` — uses SQLite's
    `json_each` to find every decision whose `content.evidence_refs`
    array contains the given evidence id. Powers the evidence-trace report.
  - `get_harness_events_for_cycle(cycle_id)` — every harness_trail row
    for a cycle, in order. Powers harness reporting.
  - `get_harness_events_for_audit_record(audit_record_id)` — jump from
    a substance event (e.g. a tool_call) to the harness verdict that
    covered it. The parallel-chain query.
  - `get_rejected_tool_calls_for_cycle(cycle_id)` — surfaces the things
    the agent tried that the Action Harness blocked. These have no
    audit_records counterpart; their presence here is the only trace.

The CTE chooses `parent_id` walking over Python recursion because it
keeps the entire traversal inside the database engine, returns results
in a single query, and is the access pattern self-referential FKs were
designed for.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select, text

from ..models.audit import AuditRecord, HarnessRecord, InternalOpRecord
from .schema import audit_records, harness_trail, operations
from .store import AuditStore


def _content(raw: Any) -> dict[str, Any]:
    """Normalize the `content` column to a dict.

    SQLAlchemy's typed `Table` select (the `select(audit_records)` form)
    runs the JSON column through its type handler and returns a dict.
    Raw `text()` queries (used for the recursive CTE in get_decision_chain
    and the json_each forward walk in get_evidence_consumers) bypass that
    handler and return the JSON column as a string. This helper handles
    both shapes so consumers don't have to.
    """
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


# ============================================================
# Cycle reads
# ============================================================
def get_cycle_events(store: AuditStore, cycle_id: str) -> list[AuditRecord]:
    """Return every audit_record for a cycle, ordered by id (the
    insertion order). Ordinary projection — no traversal."""
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(audit_records)
            .where(audit_records.c.cycle_id == cycle_id)
            .order_by(audit_records.c.id)
        ).mappings().all()
    return [_row_to_audit_record(r) for r in rows]


# ============================================================
# Report 1 — decision chain (backward walk via recursive CTE)
# ============================================================
def get_decision_chain(
    store: AuditStore,
    start_record_id: int,
) -> list[AuditRecord]:
    """Walk parent_id backward from start_record_id, returning the chain
    of decision-category records that led to it. Result is ordered from
    `start_record_id` up to the cycle root (cycle_started).

    Implemented as a recursive CTE so the entire walk happens in one
    query inside SQLite, regardless of chain depth. Evidence records on
    the chain are filtered out — Report 1 wants only the decisions.
    """
    # Use raw text() for the recursive CTE — SQLAlchemy 2.0 supports
    # cte(recursive=True) but the text form is clearer for this shape.
    cte_sql = text("""
        WITH RECURSIVE chain AS (
          SELECT * FROM audit_records WHERE id = :start_id
          UNION ALL
          SELECT r.* FROM audit_records r
          JOIN chain c ON r.id = c.parent_id
        )
        SELECT * FROM chain
        WHERE category = 'decision'
        ORDER BY id DESC
    """)
    with store.engine.connect() as conn:
        rows = conn.execute(cte_sql, {"start_id": start_record_id}).mappings().all()
    return [_row_to_audit_record(r) for r in rows]


# ============================================================
# Report 2 — evidence consumers (forward via json_each)
# ============================================================
def get_evidence_consumers(
    store: AuditStore,
    evidence_record_id: int,
) -> list[AuditRecord]:
    """Find every decision record whose content.evidence_refs array
    contains the given evidence record's id. Uses SQLite's json_each
    to expand the JSON array into rows for an exact-match join.

    Avoids the `LIKE '%id%'` substring trap: LIKE '%5%' would mis-match
    ids 15, 25, 50, etc.
    """
    sql = text("""
        SELECT DISTINCT r.*
        FROM audit_records r,
             json_each(r.content, '$.evidence_refs') refs
        WHERE refs.value = :evidence_id
          AND r.category = 'decision'
        ORDER BY r.id
    """)
    with store.engine.connect() as conn:
        rows = conn.execute(sql, {"evidence_id": evidence_record_id}).mappings().all()
    return [_row_to_audit_record(r) for r in rows]


# ============================================================
# Internal ops reads
# ============================================================
def get_evaluations_for_cycle(
    store: AuditStore,
    cycle_id: str,
) -> dict[str, list[InternalOpRecord]]:
    """Return every operations record for a cycle's evaluations,
    grouped by op_id. Each value is the ordered chain of events for
    one evaluation invocation (typically judge_call then evaluator_score).

    Multiple evaluations against the same recommendation produce
    multiple op_ids; each is its own list.
    """
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(operations)
            .where(
                (operations.c.target_cycle_id == cycle_id)
                & (operations.c.op_type == "evaluation")
            )
            .order_by(operations.c.op_id, operations.c.id)
        ).mappings().all()
    out: dict[str, list[InternalOpRecord]] = {}
    for r in rows:
        rec = _row_to_op_record(r)
        out.setdefault(rec.op_id, []).append(rec)
    return out


def get_record_by_id(store: AuditStore, record_id: int) -> AuditRecord | None:
    """Single-record lookup. Returns None if no row exists."""
    with store.engine.connect() as conn:
        row = conn.execute(
            select(audit_records).where(audit_records.c.id == record_id)
        ).mappings().fetchone()
    return _row_to_audit_record(row) if row else None


def find_recommendation_for_cycle(
    store: AuditStore,
    cycle_id: str,
) -> AuditRecord | None:
    """Return the `recommendation` record for a cycle, or None if the
    cycle hasn't produced one yet."""
    with store.engine.connect() as conn:
        row = conn.execute(
            select(audit_records)
            .where(
                (audit_records.c.cycle_id == cycle_id)
                & (audit_records.c.type == "recommendation")
            )
            .order_by(audit_records.c.id)
        ).mappings().fetchone()
    return _row_to_audit_record(row) if row else None


# ============================================================
# Harness trail reads
# ============================================================
def get_harness_events_for_cycle(
    store: AuditStore,
    cycle_id: str,
) -> list[HarnessRecord]:
    """Return every harness_trail event for a cycle, ordered by id.
    Powers harness reporting — "what did the harnesses verify or reject
    on this review?"
    """
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(harness_trail)
            .where(harness_trail.c.cycle_id == cycle_id)
            .order_by(harness_trail.c.id)
        ).mappings().all()
    return [_row_to_harness_record(r) for r in rows]


def get_harness_events_for_audit_record(
    store: AuditStore,
    audit_record_id: int,
) -> list[HarnessRecord]:
    """Return the harness verdicts that reference a specific audit_records
    row (via harness_trail.related_event_id).

    Use case: starting from a tool_call event in audit_records, find the
    Action Harness's policy-check verdict for it. Most events have either
    zero or one matching harness row, but the API returns a list to keep
    the contract permissive.
    """
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(harness_trail)
            .where(harness_trail.c.related_event_id == audit_record_id)
            .order_by(harness_trail.c.id)
        ).mappings().all()
    return [_row_to_harness_record(r) for r in rows]


def get_rejected_tool_calls_for_cycle(
    store: AuditStore,
    cycle_id: str,
) -> list[HarnessRecord]:
    """Return Action Harness tool-call rejections for a cycle. These
    have no audit_records counterpart by design — the rejection lives
    only in harness_trail. This is the query that makes that property
    visible: 'show me the things the agent tried but was not allowed
    to do.'"""
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(harness_trail)
            .where(
                (harness_trail.c.cycle_id == cycle_id)
                & (harness_trail.c.harness == "action")
                & (harness_trail.c.type == "tool_call_policy_check")
                & (harness_trail.c.verdict == "rejected")
            )
            .order_by(harness_trail.c.id)
        ).mappings().all()
    return [_row_to_harness_record(r) for r in rows]


def find_gate_verdict_for_cycle(
    store: AuditStore,
    cycle_id: str,
) -> HarnessRecord | None:
    """Return the Action Harness's recommendation-gate verdict for the
    cycle, or None if the gate hasn't run yet. There is at most one
    gate_verdict per cycle (one recommendation per cycle); the most
    recent row is returned if anything ever produces more than one."""
    with store.engine.connect() as conn:
        row = conn.execute(
            select(harness_trail)
            .where(
                (harness_trail.c.cycle_id == cycle_id)
                & (harness_trail.c.type == "gate_verdict")
            )
            .order_by(harness_trail.c.id.desc())
        ).mappings().fetchone()
    return _row_to_harness_record(row) if row else None


# ============================================================
# Row -> Pydantic helpers
# ============================================================
def _row_to_audit_record(row) -> AuditRecord:
    return AuditRecord(
        id=row["id"],
        cycle_id=row["cycle_id"],
        parent_id=row["parent_id"],
        category=row["category"],
        type=row["type"],
        agent=row["agent"],
        content=_content(row["content"]),
        timestamp=row["timestamp"],
    )


def _row_to_op_record(row) -> InternalOpRecord:
    return InternalOpRecord(
        id=row["id"],
        op_id=row["op_id"],
        op_type=row["op_type"],
        target_cycle_id=row["target_cycle_id"],
        target_record_id=row["target_record_id"],
        parent_id=row["parent_id"],
        type=row["type"],
        content=_content(row["content"]),
        timestamp=row["timestamp"],
    )


def _row_to_harness_record(row) -> HarnessRecord:
    return HarnessRecord(
        id=row["id"],
        cycle_id=row["cycle_id"],
        parent_id=row["parent_id"],
        related_event_id=row["related_event_id"],
        harness=row["harness"],
        type=row["type"],
        verdict=row["verdict"],
        content=_content(row["content"]),
        timestamp=row["timestamp"],
    )
