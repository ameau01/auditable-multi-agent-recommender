"""Read-only helpers for the audit store.

All functions take an `AuditStore` and return typed Pydantic models —
never raw rows. They never write. The write path lives in
`store.py`.

Two key reads support the two reports:

  - `get_decision_chain(start_record_id)` — recursive CTE walks parent_id
    backward, returning the chain of decision-category records that led
    to `start_record_id`. Powers Report 1 (key-decision traceability).
  - `get_evidence_consumers(evidence_record_id)` — uses SQLite's
    `json_each` to find every decision whose `content.evidence_refs`
    array contains the given evidence id. Powers Report 2 (evidence
    traceability, forward direction).

The CTE chooses `parent_id` walking over Python recursion because it
keeps the entire traversal inside the database engine, returns results
in a single query, and is the access pattern self-referential FKs were
designed for.
"""

from __future__ import annotations

from sqlalchemy import select, text

from ..models.audit import AuditRecord, InternalOpRecord
from .schema import audit_records, internal_ops
from .store import AuditStore


# ============================================================
# Cycle reads
# ============================================================
def get_cycle_events(store: AuditStore, cycle_id: str) -> list[AuditRecord]:
    """Return every audit_record for a cycle, ordered by id (the
    insertion order). Ordinary projection — no traversal."""
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(audit_records)
            .where(audit_records.c.review_cycle_id == cycle_id)
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
    """Return every internal_ops record for a cycle's evaluations,
    grouped by op_id. Each value is the ordered chain of events for
    one evaluation invocation (typically judge_call then evaluator_score).

    Multiple evaluations against the same recommendation produce
    multiple op_ids; each is its own list.
    """
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(internal_ops)
            .where(
                (internal_ops.c.target_cycle_id == cycle_id)
                & (internal_ops.c.op_type == "evaluation")
            )
            .order_by(internal_ops.c.op_id, internal_ops.c.id)
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
                (audit_records.c.review_cycle_id == cycle_id)
                & (audit_records.c.type == "recommendation")
            )
            .order_by(audit_records.c.id)
        ).mappings().fetchone()
    return _row_to_audit_record(row) if row else None


# ============================================================
# Row -> Pydantic helpers
# ============================================================
def _row_to_audit_record(row) -> AuditRecord:
    return AuditRecord(
        id=row["id"],
        review_cycle_id=row["review_cycle_id"],
        parent_id=row["parent_id"],
        category=row["category"],
        type=row["type"],
        agent=row["agent"],
        content=row["content"],
        emitted_at=row["emitted_at"],
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
        content=row["content"],
        emitted_at=row["emitted_at"],
    )
