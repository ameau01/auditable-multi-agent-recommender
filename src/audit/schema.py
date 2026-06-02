"""SQLAlchemy Core table definitions for the audit trail.

Two tables, both append-only:

  - `audit_records` — the reasoning trail (one row per event in a review
    cycle). Polymorphic via the `type` column; categorized for the two
    reports via `category`.
  - `internal_ops` — operations performed on a completed cycle's
    recommendation (eval runs, report renders). Separate audience
    (developers debugging the system) from the main audit trail
    (governance reviewers).

See `docs/audit-trail.md` for the column-level schema and rationale.

Indexes:
  - `one_start_per_cycle` (partial UNIQUE): the DB itself enforces that
    each cycle_id has at most one cycle_started row.
  - `one_end_per_cycle` (partial UNIQUE): same for cycle_completed.
  - `cycle_lookup`: covers "all events for cycle X" queries.
  - `parent_walk`: supports the recursive CTE walking parent_id chains.
  - `category_type`: supports filtering by (category, type) for reports.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    func,
    text,
)


metadata = MetaData()


# ============================================================
# audit_records — the reasoning trail
# ============================================================
audit_records = Table(
    "audit_records",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("review_cycle_id", String, nullable=False),
    Column("parent_id", Integer, ForeignKey("audit_records.id"), nullable=True),
    Column("category", String, nullable=False),
    Column("type", String, nullable=False),
    Column("agent", String, nullable=True),
    Column("content", JSON, nullable=False),
    Column(
        "emitted_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    CheckConstraint(
        "category IN ('decision', 'evidence')",
        name="ck_audit_records_category",
    ),
)


# ============================================================
# internal_ops — post-hoc operations on completed cycles
# ============================================================
internal_ops = Table(
    "internal_ops",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("op_id", String, nullable=False),
    Column("op_type", String, nullable=False),
    Column("target_cycle_id", String, nullable=False),
    Column("target_record_id", Integer, nullable=True),
    Column("parent_id", Integer, ForeignKey("internal_ops.id"), nullable=True),
    Column("type", String, nullable=False),
    Column("content", JSON, nullable=False),
    Column(
        "emitted_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
)


# ============================================================
# Indexes
# ============================================================
# Partial unique indexes — these are SQLite-specific syntax (the
# `sqlite_where` argument). SQLAlchemy generates the right DDL.

Index(
    "one_start_per_cycle",
    audit_records.c.review_cycle_id,
    unique=True,
    sqlite_where=text("type = 'cycle_started'"),
)

Index(
    "one_end_per_cycle",
    audit_records.c.review_cycle_id,
    unique=True,
    sqlite_where=text("type = 'cycle_completed'"),
)

Index(
    "cycle_lookup",
    audit_records.c.review_cycle_id,
    audit_records.c.id,
)

Index(
    "parent_walk",
    audit_records.c.parent_id,
)

Index(
    "category_type",
    audit_records.c.category,
    audit_records.c.type,
)

# internal_ops index for target_cycle_id lookups (most common query)
Index(
    "ops_by_target_cycle",
    internal_ops.c.target_cycle_id,
    internal_ops.c.op_id,
)
