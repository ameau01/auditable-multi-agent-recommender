"""Tests for ReasoningHarness.

Covers three pre-emit checks:
  - check_finding_type rejects values outside the four-valued set.
  - check_evidence_refs_minimum enforces non-empty refs on issue_found,
    short-circuits the threshold for non-issue finding_types.
  - check_evaluator_drift_verdicts rejects values outside {tight, loose,
    contradictory}.
"""

from __future__ import annotations

import pytest

from src.audit import AuditStore
from src.audit.queries import get_harness_events_for_cycle
from src.harnesses.reasoning import ReasoningCheckResult, ReasoningHarness


# ============================================================
# check_finding_type
# ============================================================
@pytest.mark.parametrize("ftype", [
    "issue_found",
    "no_issue_found",
    "insufficient_data",
    "diagnostic_deferral",
])
def test_accepts_every_valid_finding_type(
    store: AuditStore, cycle_id: str, ftype: str,
) -> None:
    h = ReasoningHarness(store)
    result = h.check_finding_type(cycle_id, {"finding_type": ftype})
    assert isinstance(result, ReasoningCheckResult)
    assert result.passed
    assert result.verdict == "passed"


@pytest.mark.parametrize("bogus", [
    "issue",            # truncated
    "ISSUE_FOUND",      # wrong case
    None,
    "",
    "needs_investigation",  # plausible-sounding but not in the set
])
def test_rejects_invalid_finding_type(
    store: AuditStore, cycle_id: str, bogus,
) -> None:
    h = ReasoningHarness(store)
    result = h.check_finding_type(cycle_id, {"finding_type": bogus})
    assert not result.passed
    assert result.verdict == "rejected"


# ============================================================
# check_evidence_refs_minimum
# ============================================================
def test_issue_found_with_refs_meets_threshold(
    store: AuditStore, cycle_id: str,
) -> None:
    h = ReasoningHarness(store)
    payload = {"finding_type": "issue_found", "evidence_refs": [1, 2, 3]}
    assert h.check_evidence_refs_minimum(cycle_id, payload, minimum=2).passed
    assert h.check_evidence_refs_minimum(cycle_id, payload, minimum=3).passed


def test_issue_found_without_refs_fails(
    store: AuditStore, cycle_id: str,
) -> None:
    h = ReasoningHarness(store)
    payload = {"finding_type": "issue_found", "evidence_refs": []}
    result = h.check_evidence_refs_minimum(cycle_id, payload, minimum=1)
    assert not result.passed
    assert "issue_found" in result.failure_reason


def test_issue_found_with_fewer_refs_than_threshold_fails(
    store: AuditStore, cycle_id: str,
) -> None:
    h = ReasoningHarness(store)
    payload = {"finding_type": "issue_found", "evidence_refs": [1]}
    result = h.check_evidence_refs_minimum(cycle_id, payload, minimum=3)
    assert not result.passed


@pytest.mark.parametrize("ftype", [
    "no_issue_found",
    "insufficient_data",
    "diagnostic_deferral",
])
def test_threshold_short_circuits_for_non_issue_findings(
    store: AuditStore, cycle_id: str, ftype: str,
) -> None:
    """Findings that aren't 'issue_found' record an absence — they're
    allowed to carry zero evidence_refs and still pass the threshold."""
    h = ReasoningHarness(store)
    payload = {"finding_type": ftype, "evidence_refs": []}
    result = h.check_evidence_refs_minimum(cycle_id, payload, minimum=3)
    assert result.passed
    # Visible in the trail: the check ran but the threshold did not apply.
    assert result.passed
    events = get_harness_events_for_cycle(store, cycle_id)
    last = events[-1]
    assert last.content["details"]["applies"] is False


# ============================================================
# check_evaluator_drift_verdicts
# ============================================================
def test_all_valid_drift_verdicts_pass(store: AuditStore, cycle_id: str) -> None:
    h = ReasoningHarness(store)
    payload = {"drift_verdicts": {
        "compute_analyst": "tight",
        "data_layer_analyst": "loose",
        "network_analyst": "contradictory",
    }}
    assert h.check_evaluator_drift_verdicts(cycle_id, payload).passed


def test_any_invalid_drift_verdict_rejects(
    store: AuditStore, cycle_id: str,
) -> None:
    h = ReasoningHarness(store)
    payload = {"drift_verdicts": {
        "compute_analyst": "tight",
        "data_layer_analyst": "weird_verdict",
    }}
    result = h.check_evaluator_drift_verdicts(cycle_id, payload)
    assert not result.passed
    assert "data_layer_analyst" in result.failure_reason


def test_empty_drift_verdicts_passes_vacuously(
    store: AuditStore, cycle_id: str,
) -> None:
    h = ReasoningHarness(store)
    assert h.check_evaluator_drift_verdicts(cycle_id, {"drift_verdicts": {}}).passed


# ============================================================
# Every check writes one harness row
# ============================================================
def test_each_call_produces_one_row(store: AuditStore, cycle_id: str) -> None:
    h = ReasoningHarness(store)
    h.check_finding_type(cycle_id, {"finding_type": "issue_found"})
    h.check_evidence_refs_minimum(cycle_id, {"finding_type": "issue_found",
                                              "evidence_refs": [1]})
    h.check_evaluator_drift_verdicts(cycle_id, {"drift_verdicts":
                                                {"compute_analyst": "tight"}})
    events = get_harness_events_for_cycle(store, cycle_id)
    assert len(events) == 3
    assert all(e.harness == "reasoning" for e in events)
    assert all(e.type == "reasoning_check" for e in events)


def test_related_event_id_propagates_when_provided(
    store: AuditStore, cycle_id: str,
) -> None:
    """If the caller passes the audit_records.id of the finding being
    checked, it should land in harness_trail.related_event_id."""
    h = ReasoningHarness(store)
    h.check_finding_type(cycle_id, {"finding_type": "issue_found"},
                          related_event_id=42)
    events = get_harness_events_for_cycle(store, cycle_id)
    assert events[-1].related_event_id == 42
