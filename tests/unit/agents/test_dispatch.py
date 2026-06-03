"""Tests for dispatch_tool — the substance-vs-enforcement shim.

This is THE critical test of Phase 11a. Three scenarios:

  A. Allowed tool call → harness verdict 'passed' in harness_trail,
     tool_call + observation pair in audit_records, related_event_id
     on the harness row points to the audit tool_call.id.

  B. Rejected tool call → harness verdict 'rejected' in harness_trail
     only. NO audit_records entry. related_event_id is NULL.

  C. Tool raises → tool_call lands in audit_records, observation lands
     with error envelope, harness verdict was 'passed' (rejection
     would have prevented invocation). result.passed=False.

These three cases enforce the architectural property the project's
docs claim: the trail's substance ↔ enforcement chain is queryable
in both directions, and rejected calls leave no false positives in
audit_records.
"""

from __future__ import annotations

from src.agents.dispatch import ToolResult, dispatch_tool
from src.audit import AuditStore
from src.audit.queries import (
    get_cycle_events,
    get_harness_events_for_audit_record,
    get_harness_events_for_cycle,
    get_rejected_tool_calls_for_cycle,
)
from src.harnesses.action import ActionHarness


# ============================================================
# Case A — allowed call
# ============================================================
def test_allowed_call_writes_substance_and_enforcement(
    store: AuditStore,
    action_harness: ActionHarness,
    cycle_id: str,
    mock_mcp,
) -> None:
    mock_mcp.register("get_time_series", {"app_name": "app-08", "values": [1, 2]})

    result = dispatch_tool(
        store,
        action_harness,
        cycle_id=cycle_id,
        agent="compute_analyst",
        tool_name="get_time_series",
        arguments={"app_name": "app-08", "tier": "compute", "metric": "cpu_p95"},
    )

    assert isinstance(result, ToolResult)
    assert result.passed
    assert result.observation == {"app_name": "app-08", "values": [1, 2]}
    assert result.tool_call_record_id is not None
    assert result.observation_record_id is not None

    # Audit rows landed (cycle_started + tool_call + observation)
    events = get_cycle_events(store, cycle_id)
    types = [e.type for e in events]
    assert types == ["cycle_started", "tool_call", "observation"]

    # Harness row references the audit tool_call id
    harness_for_audit = get_harness_events_for_audit_record(
        store, result.tool_call_record_id,
    )
    assert len(harness_for_audit) == 1
    assert harness_for_audit[0].verdict == "passed"


# ============================================================
# Case B — rejected call
# ============================================================
def test_rejected_call_writes_only_harness_row(
    store: AuditStore,
    action_harness: ActionHarness,
    cycle_id: str,
    mock_mcp,
) -> None:
    # compute_analyst cannot call get_top_queries (out of scope).
    result = dispatch_tool(
        store,
        action_harness,
        cycle_id=cycle_id,
        agent="compute_analyst",
        tool_name="get_top_queries",
        arguments={"app_name": "app-08"},
    )

    assert not result.passed
    assert result.observation is None
    assert result.tool_call_record_id is None
    assert result.observation_record_id is None
    assert result.rejection_reason is not None

    # MCP adapter was never called
    assert mock_mcp.calls == []

    # audit_records has ONLY the cycle_started row — no tool_call,
    # no observation. The rejection lives only in harness_trail.
    events = get_cycle_events(store, cycle_id)
    types = [e.type for e in events]
    assert types == ["cycle_started"], (
        f"rejected call leaked a substance row: {types}"
    )

    # The rejection shows up in the dedicated query
    rejections = get_rejected_tool_calls_for_cycle(store, cycle_id)
    assert len(rejections) == 1
    assert rejections[0].content["tool_name"] == "get_top_queries"


# ============================================================
# Case C — tool raises
# ============================================================
def test_tool_exception_lands_in_observation_with_error(
    store: AuditStore,
    action_harness: ActionHarness,
    cycle_id: str,
    mock_mcp,
) -> None:
    mock_mcp.register_raise("get_time_series", RuntimeError("boom"))

    result = dispatch_tool(
        store,
        action_harness,
        cycle_id=cycle_id,
        agent="compute_analyst",
        tool_name="get_time_series",
        arguments={"app_name": "app-08", "tier": "compute", "metric": "cpu_p95"},
    )

    # Tool call landed (we did invoke), observation landed with error,
    # but passed=False so caller knows not to use the observation.
    assert not result.passed
    assert result.observation is None
    assert result.tool_call_record_id is not None
    assert result.observation_record_id is not None
    assert "RuntimeError" in result.rejection_reason

    events = get_cycle_events(store, cycle_id)
    obs = [e for e in events if e.type == "observation"]
    assert len(obs) == 1
    assert obs[0].content.get("error", "").startswith("RuntimeError")


# ============================================================
# Parent linkage
# ============================================================
def test_observation_parent_id_points_to_tool_call(
    store: AuditStore,
    action_harness: ActionHarness,
    cycle_id: str,
    mock_mcp,
) -> None:
    """The audit observation row's parent_id MUST be the audit
    tool_call row's id. This is what makes the per-tool-call walk
    work (an observation citation resolves through its parent)."""
    mock_mcp.register("get_time_series", {"x": 1})
    result = dispatch_tool(
        store, action_harness,
        cycle_id=cycle_id,
        agent="compute_analyst",
        tool_name="get_time_series",
        arguments={"app_name": "app-08", "tier": "compute", "metric": "cpu_p95"},
    )
    events = get_cycle_events(store, cycle_id)
    obs = next(e for e in events if e.type == "observation")
    assert obs.parent_id == result.tool_call_record_id


# ============================================================
# One call → one harness row regardless of outcome
# ============================================================
def test_every_call_produces_exactly_one_harness_row(
    store: AuditStore,
    action_harness: ActionHarness,
    cycle_id: str,
    mock_mcp,
) -> None:
    """No double-counting; no missing rows on any code path."""
    mock_mcp.register("get_time_series", {"x": 1})
    dispatch_tool(
        store, action_harness,
        cycle_id=cycle_id,
        agent="compute_analyst",
        tool_name="get_time_series",
        arguments={"app_name": "app-08", "tier": "compute", "metric": "cpu_p95"},
    )
    dispatch_tool(
        store, action_harness,
        cycle_id=cycle_id,
        agent="compute_analyst",
        tool_name="get_top_queries",  # rejected
        arguments={"app_name": "app-08"},
    )
    harness_events = get_harness_events_for_cycle(store, cycle_id)
    # 2 tool_call_policy_check rows (one passed, one rejected)
    assert len(harness_events) == 2
    assert {h.verdict for h in harness_events} == {"passed", "rejected"}
