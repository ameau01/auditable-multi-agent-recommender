"""AuditStore — the single class callers use to persist audit records.

Responsibilities:
  - Resolve the SQLite database path from AUDIT_DB_PATH env (or override),
    creating the parent directory if missing.
  - Open a SQLAlchemy engine with `PRAGMA foreign_keys = ON;` on every
    connection — SQLite does not enforce FKs by default, and the audit
    trail's "every parent reference resolves" claim depends on this.
  - Idempotently initialize the schema (CREATE TABLE IF NOT EXISTS).
  - Provide append-only insertion helpers; no update_* methods exist.
  - Encapsulate the cycle_id uniqueness invariant via start_cycle().
  - Provide the evaluator integration via evaluate_recommendation().

See `docs/audit-trail.md` for the full design.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from ..models.audit import AuditRecord, HarnessRecord, InternalOpRecord
from .schema import audit_records, harness_trail, internal_ops, metadata


# Default path matches the .hf_cache/ pattern: hidden, project-local,
# gitignored. Docker overrides via AUDIT_DB_PATH env.
DEFAULT_AUDIT_DB_PATH = ".audit_db/audit.db"

# Sentinel for in-memory operation. Callers (typically tests) pass this
# to bypass all filesystem work — no dotenv lookup, no path resolution,
# no mkdir. Pairs with a StaticPool so one shared in-memory DB persists
# across the engine's connections.
IN_MEMORY = ":memory:"


def _resolve_db_path(explicit: str | None) -> Path:
    """Resolve the audit DB path. Precedence: explicit arg, then
    AUDIT_DB_PATH env, then the default. Relative paths are anchored to
    the project root (the parent of the `src/` directory).

    When `explicit` is set, `load_dotenv()` is skipped — the caller is
    providing the path directly, so reading the env would be wasted
    work. This matters for tests, which create fresh stores in a loop
    and would otherwise pay the dotenv-walk cost (a filesystem walk
    upward from cwd) per fixture.
    """
    if explicit is not None:
        raw = explicit
    else:
        load_dotenv()
        raw = os.environ.get("AUDIT_DB_PATH", DEFAULT_AUDIT_DB_PATH)
    p = Path(raw)
    if not p.is_absolute():
        # src/audit/store.py -> walk up two parents to find project root.
        project_root = Path(__file__).resolve().parent.parent.parent
        p = project_root / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _enable_sqlite_fks(dbapi_conn, _connection_record) -> None:
    """SQLAlchemy event hook: enforce foreign keys on every SQLite
    connection. Without this, parent_id constraints are silently ignored
    and the 'every reference resolves' property of the audit trail is
    not actually enforced by the database."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def _new_cycle_id() -> str:
    """Generate a cycle_id of the form cycle_<ISO_seconds>_<8hex>.
    ~4 billion possible values per second of wall-clock — essentially
    collision-free at the rates this project ever runs."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(4)
    return f"cycle_{ts}_{suffix}"


def _row_id(result) -> int:
    """Extract the new row's integer id from a single-row INSERT result.

    SQLAlchemy types `Result.inserted_primary_key` as `Row | None`, so
    mypy can't index it directly without a narrowing check. On a
    successful single-row INSERT the value is always a non-None Row whose
    first element is the autoincrement primary key — this helper makes
    that invariant explicit and raises if the assumption is ever wrong.
    """
    pk = result.inserted_primary_key
    if pk is None:
        raise RuntimeError(
            "INSERT did not return a primary key (unexpected for an "
            "autoincrement INTEGER PRIMARY KEY)"
        )
    return int(pk[0])


def _new_op_id(op_type: str) -> str:
    """Generate an op_id for internal_ops. Same shape as cycle_id but
    prefixed with the op_type for at-a-glance recognition."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(4)
    prefix = {
        "evaluation": "eval",
        "report_render": "render",
        "evidence_render": "render",
    }.get(op_type, "op")
    return f"{prefix}_{ts}_{suffix}"


# ============================================================
# AuditStore
# ============================================================
class AuditStore:
    """Append-only audit persistence backed by SQLite.

    Construct once per process. Reuse the same instance across writes
    for connection pooling. Thread-safe at the SQLAlchemy level for
    SQLite's default isolation; single-writer assumption suffices for
    this project's scale.
    """

    def __init__(self, db_path: str | None = None) -> None:
        # In-memory short-circuit. Skips dotenv, path resolution, and
        # mkdir entirely. Uses StaticPool + check_same_thread=False so
        # a single shared in-memory database lives for the engine's
        # lifetime — without that, each new connection would see its
        # own empty `:memory:` instance.
        if db_path == IN_MEMORY:
            self.db_path = Path(IN_MEMORY)
            self._engine: Engine = create_engine(
                "sqlite:///:memory:",
                future=True,
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
        else:
            self.db_path = _resolve_db_path(db_path)
            self._engine = create_engine(
                f"sqlite:///{self.db_path}",
                future=True,
            )
        # Register the FK-enforcement hook on every new connection.
        event.listen(self._engine, "connect", _enable_sqlite_fks)

    # --------------------------------------------------------
    # Schema lifecycle
    # --------------------------------------------------------
    def initialize(self) -> None:
        """Idempotent: create tables and indexes if they do not exist.
        Safe to call on every process start."""
        metadata.create_all(self._engine)

    # --------------------------------------------------------
    # Cycle lifecycle (audit_records table)
    # --------------------------------------------------------
    def start_cycle(
        self,
        application_id: str,
        trigger_type: str = "manual",
        scenario_hash: str | None = None,
        notes: str | None = None,
        agent: str = "supervisor",
    ) -> str:
        """Begin a new review cycle. Generates a fresh cycle_id, inserts
        the cycle_started record (parent_id=NULL), and returns the cycle_id.

        Raises sqlalchemy.exc.IntegrityError if (somehow) the cycle_id
        collides — caller can retry. Collision probability is ~0 at
        portfolio scale.
        """
        cycle_id = _new_cycle_id()
        content = {
            "application_id": application_id,
            "trigger_type": trigger_type,
            "scenario_hash": scenario_hash,
            "notes": notes,
        }
        with self._engine.begin() as conn:
            conn.execute(
                insert(audit_records).values(
                    review_cycle_id=cycle_id,
                    parent_id=None,
                    category="decision",
                    type="cycle_started",
                    agent=agent,
                    content=content,
                )
            )
        return cycle_id

    def add_event(self, record: AuditRecord) -> int:
        """Append one event to audit_records. Returns the inserted row id.

        The record's id and emitted_at fields are ignored (populated by
        SQLite); the rest of the fields are inserted as given.

        Raises pydantic.ValidationError if the record doesn't validate.
        Raises sqlalchemy.exc.IntegrityError on FK violation (bogus
        parent_id) or unique-index violation (duplicate cycle_started /
        cycle_completed for the same cycle_id).
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(audit_records).values(
                    review_cycle_id=record.review_cycle_id,
                    parent_id=record.parent_id,
                    category=record.category,
                    type=record.type,
                    agent=record.agent,
                    content=record.content,
                )
            )
            return _row_id(result)

    # --------------------------------------------------------
    # Harness trail (harness_trail table)
    # --------------------------------------------------------
    def add_harness_event(self, record: HarnessRecord) -> int:
        """Append one event to harness_trail. Returns the inserted row id.

        Used by the three harness modules. The record's id and emitted_at
        fields are ignored (populated by SQLite); the rest are inserted
        as given.

        Raises pydantic.ValidationError if the record doesn't validate
        (e.g. unknown harness name, unknown verdict).
        Raises sqlalchemy.exc.IntegrityError if `parent_id` points to a
        nonexistent harness_trail row (PRAGMA foreign_keys=ON catches this).
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(harness_trail).values(
                    review_cycle_id=record.review_cycle_id,
                    parent_id=record.parent_id,
                    related_event_id=record.related_event_id,
                    harness=record.harness,
                    type=record.type,
                    verdict=record.verdict,
                    content=record.content,
                )
            )
            return _row_id(result)

    def complete_cycle(
        self,
        cycle_id: str,
        final_status: str = "completed",
        failure_reason: str | None = None,
        recommendation_record_id: int | None = None,
        agent: str = "supervisor",
    ) -> int:
        """Append the cycle_completed record. parent_id is set to the
        cycle_started record's id, computed via lookup.

        Raises ValueError if no cycle_started row exists for cycle_id.
        Raises IntegrityError if cycle_completed already exists for this
        cycle (caught by the one_end_per_cycle partial unique index).
        """
        with self._engine.begin() as conn:
            row = conn.execute(
                select(audit_records.c.id).where(
                    (audit_records.c.review_cycle_id == cycle_id)
                    & (audit_records.c.type == "cycle_started")
                )
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"No cycle_started record found for cycle_id={cycle_id!r}"
                )
            cycle_started_id = int(row[0])
            content = {
                "final_status": final_status,
                "failure_reason": failure_reason,
                "recommendation_record_id": recommendation_record_id,
            }
            result = conn.execute(
                insert(audit_records).values(
                    review_cycle_id=cycle_id,
                    parent_id=cycle_started_id,
                    category="decision",
                    type="cycle_completed",
                    agent=agent,
                    content=content,
                )
            )
            return _row_id(result)

    # --------------------------------------------------------
    # Internal ops (internal_ops table)
    # --------------------------------------------------------
    def add_op_event(self, record: InternalOpRecord) -> int:
        """Append one event to internal_ops. Returns the inserted row id.

        Used by callers that build their own op chains. For evaluation
        ops, prefer the higher-level evaluate_recommendation helper.
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(internal_ops).values(
                    op_id=record.op_id,
                    op_type=record.op_type,
                    target_cycle_id=record.target_cycle_id,
                    target_record_id=record.target_record_id,
                    parent_id=record.parent_id,
                    type=record.type,
                    content=record.content,
                )
            )
            return _row_id(result)

    def evaluate_recommendation(
        self,
        target_cycle_id: str,
        target_record_id: int,
        judge_call: dict[str, Any],
        score_one_result: dict[str, Any],
    ) -> str:
        """Record one evaluation run against a cycle's recommendation.

        Emits the two-event chain: judge_call (evidence within the op)
        followed by evaluator_score (decision within the op, parent_id
        pointing to the judge_call). Returns the new op_id.

        Args:
            target_cycle_id: The cycle whose recommendation is being scored.
            target_record_id: The recommendation row's id (so backward
                walks can resolve from the score back to the artifact).
            judge_call: dict matching JudgeCallContent shape.
            score_one_result: dict matching EvaluatorScoreContent.score_one_result
                shape (typically a ScoreOneResult model_dump()).
        """
        op_id = _new_op_id("evaluation")
        with self._engine.begin() as conn:
            jc_result = conn.execute(
                insert(internal_ops).values(
                    op_id=op_id,
                    op_type="evaluation",
                    target_cycle_id=target_cycle_id,
                    target_record_id=target_record_id,
                    parent_id=None,
                    type="judge_call",
                    content=judge_call,
                )
            )
            judge_call_id = _row_id(jc_result)
            conn.execute(
                insert(internal_ops).values(
                    op_id=op_id,
                    op_type="evaluation",
                    target_cycle_id=target_cycle_id,
                    target_record_id=target_record_id,
                    parent_id=judge_call_id,
                    type="evaluator_score",
                    content={
                        "score_one_result": score_one_result,
                        "judge_call_id": judge_call_id,
                    },
                )
            )
        return op_id

    # --------------------------------------------------------
    # Engine access (for queries.py and composer.py)
    # --------------------------------------------------------
    @property
    def engine(self) -> Engine:
        """Expose the underlying engine to the read-only helpers in
        queries.py and composer.py. They never write — write paths go
        through the methods above."""
        return self._engine
