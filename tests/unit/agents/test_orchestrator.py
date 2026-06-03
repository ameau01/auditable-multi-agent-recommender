"""Tests for the LangGraph orchestrator.

Covers:
  - Graph builds without raising.
  - End-to-end run (with mocked MCP) produces the expected audit shape.
  - Input Harness rejection short-circuits to cycle_complete with
    terminal_state='rejected_input'.
  - The legacy `orchestrate()` stub still raises with a pointer to the
    new runner entry point (back-compat contract).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.orchestrator import build_graph, orchestrate
from src.agents.state import CycleState
from src.audit import AuditStore
from src.audit.queries import get_cycle_events, get_harness_events_for_cycle
from src.harnesses.action import ActionHarness
from src.harnesses.input import InputHarness


# ============================================================
# Build
# ============================================================
def test_build_graph_returns_compiled_app(
    store: AuditStore,
    input_harness: InputHarness,
    action_harness: ActionHarness,
) -> None:
    app = build_graph(store, input_harness, action_harness)
    # Compiled LangGraph has an invoke method.
    assert hasattr(app, "invoke")


# ============================================================
# Happy-path skeleton run
# ============================================================
def test_agents_run_with_valid_app_produces_expected_trail(
    store: AuditStore,
    input_harness: InputHarness,
    action_harness: ActionHarness,
    mock_mcp,
    app_08_metadata: dict[str, Any],
    app_08_terraform: dict[str, Any],
) -> None:
    """End-to-end: start a cycle, invoke the graph with mocked MCP,
    verify the audit trail has every expected row in the expected order."""
    mock_mcp.register("get_scenario_metadata", app_08_metadata)
    mock_mcp.register("get_terraform", app_08_terraform)

    cycle_id = store.start_cycle(application_id="app-08", trigger_type="test")
    app = build_graph(store, input_harness, action_harness)
    initial = CycleState(
        application_id="app-08",
        cycle_id=cycle_id,
        cycle_started_id=store.get_cycle_started_id(cycle_id),
    )
    final = app.invoke(initial)

    terminal = (
        final.get("terminal_state") if isinstance(final, dict)
        else final.terminal_state
    )
    assert terminal == "no_specialists"

    # Expected audit row shape under the supervisor-as-router pattern:
    # cycle_started + 2 tool_call + 2 observation + system_mapper_output
    # + 2 supervisor_decision (one dispatch_system_mapper, one complete).
    events = get_cycle_events(store, cycle_id)
    type_counts: dict[str, int] = {}
    for e in events:
        type_counts[e.type] = type_counts.get(e.type, 0) + 1
    assert type_counts.get("cycle_started") == 1
    assert type_counts.get("tool_call") == 2
    assert type_counts.get("observation") == 2
    assert type_counts.get("system_mapper_output") == 1
    # Two supervisor decisions: dispatch_system_mapper, then complete.
    assert type_counts.get("supervisor_decision") == 2
    sup_rows = [e for e in events if e.type == "supervisor_decision"]
    decision_types = [r.content["decision_type"] for r in sup_rows]
    assert decision_types == ["dispatch_system_mapper", "complete"]
    assert sup_rows[-1].content["terminal_state"] == "no_specialists"

    # Harness rows: 2 input_validation (passed), 2 tool_call_policy_check
    # (passed), 3 reasoning_check decision_evidence_backed (passed): one
    # for system_mapper_output, two for supervisor_decision. 7 total.
    h_events = get_harness_events_for_cycle(store, cycle_id)
    assert len(h_events) == 7
    assert all(h.verdict == "passed" for h in h_events)
    rc = [h for h in h_events if h.type == "reasoning_check"]
    assert len(rc) == 3
    assert all(r.content["check_name"] == "decision_evidence_backed" for r in rc)
    # The harness records the decision's type as target_event_type so
    # readers can distinguish "verified a supervisor_decision" from
    # "verified a system_mapper_output" without joining audit_records.
    target_types = sorted(r.content["target_event_type"] for r in rc)
    assert target_types == [
        "supervisor_decision",
        "supervisor_decision",
        "system_mapper_output",
    ]

    # Bug-fix lock-in (Phase 11a.3 follow-up): the system_mapper_output
    # row cites the observation rows it was derived from. With two MCP
    # fetches (get_scenario_metadata + get_terraform), evidence_refs has
    # exactly two ids and both resolve to observation rows in this cycle.
    sm_rows = [e for e in events if e.type == "system_mapper_output"]
    assert len(sm_rows) == 1
    sm_evidence = sm_rows[0].content.get("evidence_refs") or []
    assert len(sm_evidence) == 2
    obs_ids = {e.id for e in events if e.type == "observation"}
    assert set(sm_evidence).issubset(obs_ids)


# ============================================================
# Input rejection short-circuit
# ============================================================
def test_input_rejection_short_circuits(
    store: AuditStore,
    input_harness: InputHarness,
    action_harness: ActionHarness,
) -> None:
    """Bogus app-name → Input Harness rejects → graph routes straight
    to cycle_complete. System Mapper never runs; no tool_call rows."""
    cycle_id = store.start_cycle(application_id="bogus_app", trigger_type="test")
    app = build_graph(store, input_harness, action_harness)
    initial = CycleState(application_id="bogus_app", cycle_id=cycle_id)
    final = app.invoke(initial)

    terminal = (
        final.get("terminal_state") if isinstance(final, dict)
        else final.terminal_state
    )
    assert terminal == "rejected_input"

    events = get_cycle_events(store, cycle_id)
    types = {e.type for e in events}
    # cycle_started only — no system_mapper rows, no tool_calls
    assert types == {"cycle_started"}

    h_events = get_harness_events_for_cycle(store, cycle_id)
    rejected = [h for h in h_events if h.verdict == "rejected"]
    assert len(rejected) >= 1


# ============================================================
# Legacy stub contract (back-compat)
# ============================================================
def test_legacy_orchestrate_stub_still_raises_with_pointer() -> None:
    """The old `orchestrate(scenario)` entry remains importable and
    raises with a pointer to `run_cycle`. Keeps any pre-agent-cycle caller
    from silently breaking; the error tells them where to go."""
    with pytest.raises(NotImplementedError) as exc:
        orchestrate({"scenario_id": "08"})
    msg = str(exc.value)
    assert "run_cycle" in msg
    assert "CHANGELOG" in msg
