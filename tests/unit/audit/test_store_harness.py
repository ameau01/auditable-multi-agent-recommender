"""Tests for AuditStore.add_harness_event and the harness_trail table.

Covers:
  - Round-trip insert -> read with all four record types.
  - Verdict CHECK constraint rejects bogus verdicts.
  - Harness CHECK constraint rejects bogus harness names.
  - parent_id self-FK enforced (PRAGMA foreign_keys=ON).
  - related_event_id is NOT a hard FK (rejected tool calls can carry
    NULL when no audit_records row exists by design).
  - No update_/delete_ harness methods on the store.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.audit import AuditStore
from src.models.audit import HarnessRecord


def _h(cycle_id: str, **overrides) -> HarnessRecord:
    """Build a default HarnessRecord with optional field overrides."""
    base = dict(
        cycle_id=cycle_id,
        parent_id=None,
        related_event_id=None,
        harness="action",
        type="tool_call_policy_check",
        verdict="passed",
        content={"agent": "compute_analyst", "tool_name": "get_time_series"},
    )
    base.update(overrides)
    return HarnessRecord(**base)


# ============================================================
# Basic insert
# ============================================================
def test_add_harness_event_inserts_and_returns_id(store: AuditStore) -> None:
    cid = store.start_cycle(application_id="app-08")
    rid = store.add_harness_event(_h(cid))
    assert isinstance(rid, int)
    assert rid > 0


def test_round_trip_with_minimum_fields(store: AuditStore) -> None:
    """Round-trip via raw SQL: confirm the row landed with the values
    we inserted, in the columns we expect."""
    cid = store.start_cycle(application_id="app-08")
    store.add_harness_event(_h(cid, harness="input", type="input_validation"))
    with store.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT cycle_id, harness, type, verdict "
                "FROM harness_trail WHERE cycle_id = :c"
            ),
            {"c": cid},
        ).fetchone()
    assert row is not None
    assert row[0] == cid
    assert row[1] == "input"
    assert row[2] == "input_validation"
    assert row[3] == "passed"


def test_all_four_record_types_can_be_inserted(store: AuditStore) -> None:
    """Sanity: each of the four HarnessRecordType values is acceptable."""
    cid = store.start_cycle(application_id="app-08")
    for rtype, harness in [
        ("input_validation",       "input"),
        ("tool_call_policy_check", "action"),
        ("gate_verdict",           "action"),
        ("reasoning_check",        "reasoning"),
    ]:
        rid = store.add_harness_event(
            _h(cid, type=rtype, harness=harness)
        )
        assert rid > 0


def test_all_four_verdicts_can_be_recorded(store: AuditStore) -> None:
    cid = store.start_cycle(application_id="app-08")
    for verdict in ("passed", "rejected", "flagged", "info"):
        rid = store.add_harness_event(_h(cid, verdict=verdict))
        assert rid > 0


# ============================================================
# Constraints
# ============================================================
def test_bogus_verdict_rejected_by_check_constraint(store: AuditStore) -> None:
    cid = store.start_cycle(application_id="app-08")
    # Bypass Pydantic via raw insert — the CHECK constraint is the
    # SQLite-level guarantee independent of Pydantic.
    with store.engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO harness_trail "
                "(cycle_id, harness, type, verdict, content) "
                "VALUES (:c, 'action', 'tool_call_policy_check', 'maybe', '{}')"
            ),
            {"c": cid},
        )


def test_bogus_harness_rejected_by_check_constraint(store: AuditStore) -> None:
    cid = store.start_cycle(application_id="app-08")
    with store.engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO harness_trail "
                "(cycle_id, harness, type, verdict, content) "
                "VALUES (:c, 'fifth_harness', 'tool_call_policy_check', 'passed', '{}')"
            ),
            {"c": cid},
        )


def test_parent_id_fk_enforced(store: AuditStore) -> None:
    """Self-FK on parent_id: pointing to a nonexistent row fails under
    PRAGMA foreign_keys=ON."""
    cid = store.start_cycle(application_id="app-08")
    bogus = _h(cid, parent_id=99999)
    with pytest.raises(IntegrityError):
        store.add_harness_event(bogus)


def test_related_event_id_not_enforced_as_fk(store: AuditStore) -> None:
    """related_event_id is intentionally NOT a FK constraint: a rejected
    tool call has no audit_records row, so the column must accept NULL
    (and arbitrary numbers without referential check) so the rejection
    can still be recorded."""
    cid = store.start_cycle(application_id="app-08")
    # A nonexistent audit_records id is fine here:
    rid = store.add_harness_event(_h(cid, related_event_id=999_999))
    assert rid > 0


# ============================================================
# Append-only discipline (harness side too)
# ============================================================
def test_no_harness_update_or_delete_methods(store: AuditStore) -> None:
    """The harness-trail write surface should not expose mutation."""
    bad = [
        name for name in dir(store)
        if not name.startswith("_")
        and "harness" in name.lower()
        and any(verb in name.lower() for verb in ("update", "delete", "remove"))
    ]
    assert bad == [], (
        "AuditStore exposes harness mutation methods which violates "
        f"append-only discipline: {bad}"
    )
