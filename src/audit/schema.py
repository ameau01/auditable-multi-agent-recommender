"""SQLAlchemy Core table definitions for the audit trail.

Three tables, all append-only:

  - `audit_records` — the reasoning trail (one row per event in a
    review cycle). Polymorphic via the `type` column; categorized for
    the decision-vs-evidence reports via `category`.
  - `harness_trail` — enforcement events (Input Harness validations,
    Action Harness policy checks and gate verdicts, Reasoning Harness
    pre-emit checks). When a tool call is rejected, this is the only
    table that records it — its absence from audit_records is itself
    an audit signal.
  - `operations` — post-hoc operations on a completed cycle's
    recommendation (eval runs, report renders). Separate audience
    (developers debugging the system) from the main audit trail
    (human reviewers). Was named `internal_ops` until Phase 11a.2.

Column conventions (used by all three tables):

  - `cycle_id`  : every row is tied to one review cycle. (Was
                   `review_cycle_id` until Phase 11a.2.) The
                   `operations` table uses `target_cycle_id` for
                   the cycle being operated on, since "this op
                   targets that cycle" is the relationship.
  - `timestamp` : when the row was written. SQLite fills it via
                   server-default. (Was `emitted_at` until 11a.2.)

See `docs/audit-trail.md` for the column-level schema and rationale.

Indexes:
  - `one_start_per_cycle` (partial UNIQUE): the DB itself enforces
    that each cycle_id has at most one cycle_started row.
  - `one_end_per_cycle` (partial UNIQUE): same for cycle_completed.
  - `cycle_lookup`: covers "all events for cycle X" queries.
  - `parent_walk`: supports the recursive CTE walking parent_id chains.
  - `category_type`: supports filtering by (category, type) for reports.
  - `harness_cycle_lookup`: harness_trail per-cycle scan.
  - `harness_related_event`: jump from an audit_record id to its
    harness verdict (e.g. "what policy check covered tool_call 47?").
  - `harness_parent_walk`: support self-FK chains.
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
    Column("cycle_id", String, nullable=False),
    Column("parent_id", Integer, ForeignKey("audit_records.id"), nullable=True),
    Column("category", String, nullable=False),
    Column("type", String, nullable=False),
    Column("agent", String, nullable=True),
    Column("content", JSON, nullable=False),
    Column(
        "timestamp",
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
# harness_trail — enforcement events from the three harnesses
# ============================================================
# Distinct table from audit_records: the substance the agent worked
# on (tool calls, findings, decisions) lives there; the enforcement
# decisions (what was checked, allowed, or rejected) live here. A
# rejected tool call has only a harness_trail row — its absence from
# audit_records is the audit signal. See docs/audit-trail.md.
harness_trail = Table(
    "harness_trail",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("cycle_id", String, nullable=False),
    Column("parent_id", Integer, ForeignKey("harness_trail.id"), nullable=True),
    # related_event_id is a denormalized reference into audit_records.id.
    # Intentionally NOT a FK constraint so a rejected tool call (no audit
    # row exists) can still record related_event_id=NULL without violating
    # referential integrity, and so PRAGMA foreign_keys=ON does not need
    # cross-table awareness here. Application-level discipline is sufficient:
    # AuditStore is the sole writer of both tables.
    Column("related_event_id", Integer, nullable=True),
    Column("harness", String, nullable=False),
    Column("type", String, nullable=False),
    Column("verdict", String, nullable=False),
    Column("content", JSON, nullable=False),
    Column(
        "timestamp",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    CheckConstraint(
        "harness IN ('input', 'action', 'reasoning', 'orchestration')",
        name="ck_harness_trail_harness",
    ),
    CheckConstraint(
        "verdict IN ('passed', 'rejected', 'flagged', 'info')",
        name="ck_harness_trail_verdict",
    ),
)


# ============================================================
# operations — post-hoc operations on completed cycles
# ============================================================
# Renamed from `internal_ops` in Phase 11a.2. The Python identifier
# below (`operations`) matches the SQL table name; older code that
# imported `internal_ops` needs to update its import.
operations = Table(
    "operations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("op_id", String, nullable=False),
    Column("op_type", String, nullable=False),
    Column("target_cycle_id", String, nullable=False),
    Column("target_record_id", Integer, nullable=True),
    Column("parent_id", Integer, ForeignKey("operations.id"), nullable=True),
    Column("type", String, nullable=False),
    Column("content", JSON, nullable=False),
    Column(
        "timestamp",
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
    audit_records.c.cycle_id,
    unique=True,
    sqlite_where=text("type = 'cycle_started'"),
)

Index(
    "one_end_per_cycle",
    audit_records.c.cycle_id,
    unique=True,
    sqlite_where=text("type = 'cycle_completed'"),
)

Index(
    "cycle_lookup",
    audit_records.c.cycle_id,
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

# operations: per-target-cycle lookup (most common query — "show me
# every eval against this recommendation").
Index(
    "ops_by_target_cycle",
    operations.c.target_cycle_id,
    operations.c.op_id,
)

# harness_trail: per-cycle scan covers "show me everything the harnesses
# verified in cycle X."
Index(
    "harness_cycle_lookup",
    harness_trail.c.cycle_id,
    harness_trail.c.id,
)

# harness_trail: jump from an audit_records row to the harness verdict
# that covered it. Powers the "for this tool_call, what did the Action
# Harness decide?" query.
Index(
    "harness_related_event",
    harness_trail.c.related_event_id,
)

# harness_trail: support the self-FK chains (a gate_verdict whose
# sub-checks chain via parent_id).
Index(
    "harness_parent_walk",
    harness_trail.c.parent_id,
)
