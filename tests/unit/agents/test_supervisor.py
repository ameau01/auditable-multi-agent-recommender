"""Tests for the Supervisor node.

Covers:
  - Decision: skeleton run invokes zero specialists, regardless of plan.
  - Audit row written with the right decision_type and details.
  - Raises SupervisorError when state has no analysis_plan.
  - Reasoning Harness rejects decisions with missing/dangling evidence.
"""

from __future__ import annotations

import pytest

from src.agents.analysis_plan import AnalysisPlan
from src.agents.state import CycleState
from src.agents.supervisor import SupervisorError, SupervisorNode
from src.audit import AuditStore
from src.audit.queries import get_cycle_events
from src.harnesses.reasoning import ReasoningHarness
from src.models.audit import AuditRecord


def _state_with_plan(
    cycle_id: str,
    specialists: list[str],
    *,
    store: AuditStore | None = None,
) -> CycleState:
    """Build a CycleState whose Supervisor decision will satisfy the
    Reasoning Harness's evidence-backed gate.

    The Supervisor in 11a cites `last_system_mapper_output_id` as
    evidence on every decision. The fixture writes a fake
    system_mapper_output row and stamps its id on the state so the
    harness check resolves to a real row.
    """
    plan = AnalysisPlan(
        application_id="app-08",
        tiers_detected=["compute", "database"],
        specialists_to_invoke=specialists,  # type: ignore[arg-type]
    )
    last_sm_id: int | None = None
    if store is not None:
        last_sm_id = store.add_event(AuditRecord(
            cycle_id=cycle_id,
            parent_id=None,
            category="decision",
            type="system_mapper_output",
            agent="system_mapper",
            content={
                "application_id": "app-08",
                "tiers_detected": ["compute", "database"],
                "specialists_to_invoke": specialists,
            },
        ))
    # has_system_map=True because the fixture is set up post-mapper —
    # the tests are exercising the "Supervisor decides after the map
    # exists" branch (the `complete` decision). The pre-map case (where
    # Supervisor decides `dispatch_system_mapper`) is exercised by the
    # orchestrator integration test.
    return CycleState(
        application_id="app-08",
        cycle_id=cycle_id,
        analysis_plan=plan,
        has_system_map=last_sm_id is not None,
        last_system_mapper_output_id=last_sm_id,
    )


def test_supervisor_invokes_zero_specialists_in_skeleton_mode(
    store: AuditStore, cycle_id: str,
) -> None:
    """Phase 11a: even when the plan names specialists, the Supervisor
    deliberately doesn't fan out yet."""
    node = SupervisorNode(store, ReasoningHarness(store))
    state = _state_with_plan(cycle_id, ["compute_analyst", "data_layer_analyst"], store=store)

    update = node.run(state)
    assert update["specialists_invoked"] == []


def test_supervisor_audit_row_records_decision(
    store: AuditStore, cycle_id: str,
) -> None:
    node = SupervisorNode(store, ReasoningHarness(store))
    state = _state_with_plan(cycle_id, ["compute_analyst"], store=store)
    node.run(state)

    events = get_cycle_events(store, cycle_id)
    sup = [e for e in events if e.type == "supervisor_decision"]
    assert len(sup) == 1
    content = sup[0].content
    # Phase 11a.3: typed SupervisorDecisionContent — decision_type is
    # always "complete" today; the specific terminal state is on its
    # own field. "no_specialists" is a terminal_state, not a decision_type.
    assert content["decision_type"] == "complete"
    assert content["terminal_state"] == "no_specialists"
    assert content["targets"] == []
    assert "skeleton mode" in content["reason"]
    assert content["decision_details"]["plan_specialists"] == ["compute_analyst"]


def test_supervisor_with_empty_plan_still_records(
    store: AuditStore, cycle_id: str,
) -> None:
    """If the plan named no specialists, the Supervisor still writes a
    decision row (the absence-of-fan-out is itself the decision)."""
    node = SupervisorNode(store, ReasoningHarness(store))
    state = _state_with_plan(cycle_id, [], store=store)
    node.run(state)

    events = get_cycle_events(store, cycle_id)
    sup = [e for e in events if e.type == "supervisor_decision"]
    assert len(sup) == 1
    content = sup[0].content
    assert content["decision_type"] == "complete"
    assert content["terminal_state"] == "no_specialists"
    assert "named no specialists" in content["reason"]


def test_supervisor_without_evidence_raises(
    store: AuditStore, cycle_id: str,
) -> None:
    """A bare CycleState (no map, no input_validation row) means the
    Supervisor's first-call decision (dispatch_system_mapper) has no
    evidence_refs to cite, and the Reasoning Harness rejects it.

    Under the supervisor-as-router pattern, Supervisor no longer
    requires analysis_plan on entry — the first call is exactly when
    it routes to System Mapper *to* produce one. What it does require
    is at least one evidence_ref to cite, which the orchestrator
    provides via state.last_input_validation_record_id on the pass-
    through from the Input Harness.
    """
    node = SupervisorNode(store, ReasoningHarness(store))
    state = CycleState(application_id="app-08", cycle_id=cycle_id)
    with pytest.raises(SupervisorError, match="no evidence_refs"):
        node.run(state)


def test_supervisor_routes_to_system_mapper_on_first_call(
    store: AuditStore, cycle_id: str,
) -> None:
    """The supervisor-as-router pattern: the first Supervisor call
    (no system map yet) decides dispatch_system_mapper, cites the
    cycle_started audit_records row as evidence, and records a passing
    reasoning_check verdict.
    """
    state = CycleState(
        application_id="app-08",
        cycle_id=cycle_id,
        cycle_started_id=store.get_cycle_started_id(cycle_id),
        input_validation_passed=True,
    )

    node = SupervisorNode(store, ReasoningHarness(store))
    update = node.run(state)
    assert update["next_route"] == "dispatch_system_mapper"
    assert update["specialists_invoked"] == ["system_mapper"]

    events = get_cycle_events(store, cycle_id)
    sup = [e for e in events if e.type == "supervisor_decision"]
    assert len(sup) == 1
    assert sup[0].content["decision_type"] == "dispatch_system_mapper"
    assert sup[0].content["targets"] == ["system_mapper"]
