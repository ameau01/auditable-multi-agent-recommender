"""Tests for the harness_trail query helpers.

Covers:
  - get_harness_events_for_cycle returns events for the right cycle in order.
  - get_harness_events_for_audit_record returns only the verdicts tied to a
    specific audit_records.id via related_event_id.
  - get_rejected_tool_calls_for_cycle returns only Action Harness rejections.
  - find_gate_verdict_for_cycle returns the gate row (most-recent if many).
"""

from __future__ import annotations

from src.audit import AuditStore
from src.audit.queries import (
    find_gate_verdict_for_cycle,
    get_harness_events_for_audit_record,
    get_harness_events_for_cycle,
    get_rejected_tool_calls_for_cycle,
)
from src.models.audit import AuditRecord, HarnessRecord


def _audit_tool_call(cid: str) -> AuditRecord:
    return AuditRecord(
        cycle_id=cid, parent_id=None, category="evidence",
        type="tool_call", agent="compute_analyst",
        content={"tool_name": "get_time_series", "arguments": {}},
    )


def _harness(cid: str, **overrides) -> HarnessRecord:
    base = dict(
        cycle_id=cid,
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
# get_harness_events_for_cycle
# ============================================================
def test_returns_events_for_target_cycle_in_order(store: AuditStore) -> None:
    cid1 = store.start_cycle("app-08")
    cid2 = store.start_cycle("app-02")
    id1 = store.add_harness_event(_harness(cid1, type="input_validation",
                                            harness="input"))
    id2 = store.add_harness_event(_harness(cid2))  # different cycle
    id3 = store.add_harness_event(_harness(cid1, verdict="rejected"))
    events = get_harness_events_for_cycle(store, cid1)
    assert [e.id for e in events] == [id1, id3]
    # cycle 2 still isolated:
    assert id2 not in {e.id for e in events}


def test_returns_empty_list_for_unknown_cycle(store: AuditStore) -> None:
    assert get_harness_events_for_cycle(store, "cycle_doesnotexist_x") == []


# ============================================================
# get_harness_events_for_audit_record
# ============================================================
def test_resolves_audit_record_to_its_harness_verdicts(store: AuditStore) -> None:
    """The parallel-chain query: from a tool_call event, find the
    Action Harness policy-check row that covered it."""
    cid = store.start_cycle("app-08")
    audit_id = store.add_event(_audit_tool_call(cid))
    h_id = store.add_harness_event(_harness(cid, related_event_id=audit_id))
    # An unrelated harness row that does not reference this audit row:
    store.add_harness_event(_harness(cid, related_event_id=None))

    found = get_harness_events_for_audit_record(store, audit_id)
    assert [e.id for e in found] == [h_id]
    assert found[0].related_event_id == audit_id


def test_unrelated_audit_record_returns_empty(store: AuditStore) -> None:
    cid = store.start_cycle("app-08")
    audit_id = store.add_event(_audit_tool_call(cid))
    # No harness rows referencing this audit row.
    assert get_harness_events_for_audit_record(store, audit_id) == []


# ============================================================
# get_rejected_tool_calls_for_cycle
# ============================================================
def test_rejections_only(store: AuditStore) -> None:
    """The 'audit signal of absence' query — list things the harness
    prevented from running. Must filter to (action, tool_call_policy_check,
    rejected)."""
    cid = store.start_cycle("app-08")
    # An allowed tool call: harness verdict 'passed', should NOT appear.
    audit_id = store.add_event(_audit_tool_call(cid))
    store.add_harness_event(_harness(cid, related_event_id=audit_id, verdict="passed"))
    # A rejected tool call: only harness row, no audit row.
    store.add_harness_event(_harness(
        cid,
        related_event_id=None,
        verdict="rejected",
        content={"agent": "compute_analyst",
                  "tool_name": "get_top_queries",
                  "rejection_reason": "out of compute scope"},
    ))
    # A gate verdict (also Action, also 'passed') — should NOT appear.
    store.add_harness_event(_harness(
        cid, type="gate_verdict", verdict="passed",
        content={"target_record_id": 0, "overall_verdict": "passed"},
    ))
    # A reasoning_check rejection (different harness) — should NOT appear.
    store.add_harness_event(_harness(
        cid, harness="reasoning", type="reasoning_check", verdict="rejected",
        content={"check_name": "evidence_refs_minimum",
                  "failure_reason": "0 < 1"},
    ))

    rejs = get_rejected_tool_calls_for_cycle(store, cid)
    assert len(rejs) == 1
    assert rejs[0].verdict == "rejected"
    assert rejs[0].content["tool_name"] == "get_top_queries"


# ============================================================
# find_gate_verdict_for_cycle
# ============================================================
def test_finds_the_gate_verdict(store: AuditStore) -> None:
    cid = store.start_cycle("app-08")
    # Other harness rows that should be ignored:
    store.add_harness_event(_harness(cid))                  # tool_call_policy_check
    store.add_harness_event(_harness(cid, harness="input",
                                      type="input_validation"))
    # The actual gate verdict:
    g_id = store.add_harness_event(_harness(
        cid, type="gate_verdict", verdict="passed",
        content={"target_record_id": 17, "overall_verdict": "passed"},
    ))
    found = find_gate_verdict_for_cycle(store, cid)
    assert found is not None
    assert found.id == g_id
    assert found.type == "gate_verdict"


def test_returns_none_when_no_gate_yet(store: AuditStore) -> None:
    cid = store.start_cycle("app-08")
    store.add_harness_event(_harness(cid))
    assert find_gate_verdict_for_cycle(store, cid) is None


def test_returns_most_recent_gate_if_multiple(store: AuditStore) -> None:
    """Defensive: there should normally be exactly one gate verdict per
    cycle, but if two ever land, the read returns the most recent
    (highest id)."""
    cid = store.start_cycle("app-08")
    store.add_harness_event(_harness(
        cid, type="gate_verdict", verdict="rejected",
        content={"target_record_id": 1, "overall_verdict": "rejected"},
    ))
    later = store.add_harness_event(_harness(
        cid, type="gate_verdict", verdict="passed",
        content={"target_record_id": 1, "overall_verdict": "passed"},
    ))
    found = find_gate_verdict_for_cycle(store, cid)
    assert found is not None
    assert found.id == later
    assert found.verdict == "passed"
