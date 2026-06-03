"""Tests for OrchestrationHarness.

Covers the single Phase 11a.4 check:
  - check_cycle_completion_legitimate accepts legitimate terminal_states
    and rejects the three documented invalid combinations.

Plus one symmetry test: route() is public, like the other harnesses,
and writes exactly one orchestration_check row per call.
"""

from __future__ import annotations

import pytest

from src.audit import AuditStore
from src.audit.queries import get_harness_events_for_cycle
from src.harnesses.orchestration import (
    OrchestrationCheckResult,
    OrchestrationHarness,
)


# ============================================================
# check_cycle_completion_legitimate — positive cases
# ============================================================
def test_completed_with_specialists_passes(
    store: AuditStore, cycle_id: str,
) -> None:
    """The canonical positive path: a 'completed' cycle that actually
    invoked specialists is legitimate."""
    h = OrchestrationHarness(store)
    result = h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="completed",
        failed_at_stage=None,
        specialists_invoked=["compute_analyst", "network_analyst"],
    )
    assert isinstance(result, OrchestrationCheckResult)
    assert result.passed
    assert result.verdict == "passed"
    assert result.check_name == "cycle_completion_legitimate"
    assert result.failure_reason is None


def test_no_specialists_terminal_passes(
    store: AuditStore, cycle_id: str,
) -> None:
    """'no_specialists' with no specialists invoked is the right
    pairing — supervisor decided nothing needed dispatching."""
    h = OrchestrationHarness(store)
    result = h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="no_specialists",
        failed_at_stage=None,
        specialists_invoked=[],
    )
    assert result.passed


def test_failed_with_stage_passes(
    store: AuditStore, cycle_id: str,
) -> None:
    """'failed' with a named stage is legitimate — the failure is
    attributable."""
    h = OrchestrationHarness(store)
    result = h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="failed",
        failed_at_stage="system_mapper",
        specialists_invoked=[],
    )
    assert result.passed


def test_rejected_input_with_input_harness_stage_passes(
    store: AuditStore, cycle_id: str,
) -> None:
    """'rejected_input' stamped with 'input_harness' stage is the only
    legitimate combination for that terminal."""
    h = OrchestrationHarness(store)
    result = h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="rejected_input",
        failed_at_stage="input_harness",
        specialists_invoked=[],
    )
    assert result.passed


# ============================================================
# check_cycle_completion_legitimate — rejection cases
# ============================================================
def test_completed_without_specialists_rejected(
    store: AuditStore, cycle_id: str,
) -> None:
    """Rule 1: 'completed' but no specialists invoked is illegitimate;
    the supervisor should have terminated as 'no_specialists' instead."""
    h = OrchestrationHarness(store)
    result = h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="completed",
        failed_at_stage=None,
        specialists_invoked=[],
    )
    assert not result.passed
    assert result.verdict == "rejected"
    assert result.failure_reason is not None
    assert "no specialists" in result.failure_reason


def test_failed_without_stage_rejected(
    store: AuditStore, cycle_id: str,
) -> None:
    """Rule 2: 'failed' must carry a failed_at_stage."""
    h = OrchestrationHarness(store)
    result = h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="failed",
        failed_at_stage=None,
        specialists_invoked=[],
    )
    assert not result.passed
    assert result.verdict == "rejected"
    assert result.failure_reason is not None
    assert "failed_at_stage" in result.failure_reason


@pytest.mark.parametrize("bad_stage", [
    "supervisor",
    "system_mapper",
    "evaluator",
    None,
])
def test_rejected_input_with_wrong_stage_rejected(
    store: AuditStore, cycle_id: str, bad_stage: str | None,
) -> None:
    """Rule 3: 'rejected_input' must be stamped 'input_harness'; any
    other stage (or None) is a bug because no other component can
    reject the trigger."""
    h = OrchestrationHarness(store)
    result = h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="rejected_input",
        failed_at_stage=bad_stage,
        specialists_invoked=[],
    )
    assert not result.passed
    assert result.verdict == "rejected"
    assert result.failure_reason is not None
    assert "input_harness" in result.failure_reason


# ============================================================
# Row-shape + symmetry
# ============================================================
def test_each_call_produces_one_row(
    store: AuditStore, cycle_id: str,
) -> None:
    """Every check call writes exactly one orchestration_check row,
    matching the pattern of the other harnesses."""
    h = OrchestrationHarness(store)
    h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="completed",
        failed_at_stage=None,
        specialists_invoked=["compute_analyst"],
    )
    h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="no_specialists",
        failed_at_stage=None,
        specialists_invoked=[],
    )
    rows = [
        e for e in get_harness_events_for_cycle(store, cycle_id)
        if e.harness == "orchestration"
    ]
    assert len(rows) == 2
    assert all(r.type == "orchestration_check" for r in rows)
    assert all(r.verdict == "passed" for r in rows)


def test_route_is_public_for_symmetry(
    store: AuditStore, cycle_id: str,
) -> None:
    """OrchestrationHarness.route() is public, matching the other three
    harnesses (input/action/reasoning all expose route()). Test code
    can drive it directly without going through a named check."""
    h = OrchestrationHarness(store)
    result = h.route(
        cycle_id=cycle_id,
        check_name="cycle_completion_legitimate",
        target_event_type="cycle_completed",
        related_event_id=None,
        verdict="info",
        details={"note": "manual driving for test"},
        failure_reason=None,
    )
    assert result.verdict == "info"
    assert result.passed is False   # only "passed" verdict sets passed=True
    rows = [
        e for e in get_harness_events_for_cycle(store, cycle_id)
        if e.harness == "orchestration"
    ]
    assert len(rows) == 1
    assert rows[0].content["details"]["note"] == "manual driving for test"


def test_related_event_id_propagates_when_provided(
    store: AuditStore, cycle_id: str,
) -> None:
    """If the caller provides a related_event_id (e.g. the cycle_started
    row id), the harness_trail row records it. This mirrors the
    reasoning harness's behavior and supports the eventual UPDATE
    backfill that links the verdict to its cycle_completed audit row."""
    h = OrchestrationHarness(store)
    result = h.check_cycle_completion_legitimate(
        cycle_id=cycle_id,
        final_status="no_specialists",
        failed_at_stage=None,
        specialists_invoked=[],
        related_event_id=42,
    )
    assert result.passed
    rows = [
        e for e in get_harness_events_for_cycle(store, cycle_id)
        if e.harness == "orchestration"
    ]
    assert rows[-1].related_event_id == 42
