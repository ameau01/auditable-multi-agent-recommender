"""Tests for compose_from_cycle.

Covers:
  - The composer produces a valid Composite from a cycle's records
  - The Composite's trace_section is populated from audit_records
  - The renderer (render_report, render_trace) consumes the Composite
    without raising
"""

from __future__ import annotations

import pytest

from src.audit import AuditStore
from src.audit.composer import compose_from_cycle
from src.models.audit import AuditRecord
from src.models.composite import Composite
from src.renderer import render_report, render_trace


# ============================================================
# Fixture: build a cycle that includes a full Composite
# ============================================================
@pytest.fixture
def cycle_with_recommendation(store: AuditStore) -> str:
    """A cycle whose recommendation event carries a full Composite,
    plus a supervisor_decision and a specialist_finding so the composer
    has something to put in TraceSection."""
    cid = store.start_cycle(application_id="app-08", trigger_type="test")

    # cycle_started id is 1
    sup_id = store.add_event(AuditRecord(
        cycle_id=cid, parent_id=1,
        category="decision", type="supervisor_decision", agent="supervisor",
        content={"decision_type": "dispatch_specialists",
                 "targets": ["compute_analyst"],
                 "reason": "Plan named compute_analyst.",
                 "evidence_refs": [],
                 "decision_details": {}},
    ))
    finding_id = store.add_event(AuditRecord(
        cycle_id=cid, parent_id=sup_id,
        category="decision", type="specialist_finding", agent="compute_analyst",
        content={"specialist": "compute_analyst", "finding_type": "no_issue_found",
                 "headline": "Compute is healthy"},
    ))

    # A minimal-but-valid Composite (gold-quality fields populated).
    composite_data = {
        "scenario_id": "app-08",
        "finding_type": "no_issue_found",
        "specific_change": "No changes recommended. Compute is operating within healthy bounds.",
        "primary_tier": None,
        "secondary_tier": None,
        "action_category": None,
        "evidence": {
            "telemetry_observations": ["CPU p95 at 27.1%"],
            "infrastructure_context": [],
            "correlation_observations": [],
        },
        "reasoning": "All tiers within healthy bounds; no remediation required.",
        "scoring_metadata": {
            "description": "Test cycle",
            "finding_type_allowed": ["no_issue_found"],
            "primary_tier_allowed": [None],
            "secondary_tier_allowed": [None],
            "action_category_allowed": [None],
        },
    }
    store.add_event(AuditRecord(
        cycle_id=cid, parent_id=finding_id,
        category="decision", type="recommendation", agent="supervisor",
        content={"composite": composite_data, "evidence_refs": []},
    ))

    return cid


# ============================================================
# Tests
# ============================================================
def test_compose_produces_valid_composite(
    store: AuditStore, cycle_with_recommendation: str,
) -> None:
    composite = compose_from_cycle(store, cycle_with_recommendation)
    assert isinstance(composite, Composite)
    assert composite.scenario_id == "app-08"
    assert composite.specific_change.startswith("No changes recommended")


def test_compose_populates_trace_section(
    store: AuditStore, cycle_with_recommendation: str,
) -> None:
    composite = compose_from_cycle(store, cycle_with_recommendation)
    assert composite.trace is not None
    # supervisor_decision and specialist_findings populated from events
    assert composite.trace.supervisor_decision is not None
    assert composite.trace.specialist_findings is not None
    assert len(composite.trace.specialist_findings) == 1


def test_compose_raises_when_no_recommendation(store: AuditStore) -> None:
    cid = store.start_cycle(application_id="app-08")  # no recommendation event
    with pytest.raises(ValueError, match="No recommendation record"):
        compose_from_cycle(store, cid)


def test_renderer_consumes_composed_output_without_raising(
    store: AuditStore, cycle_with_recommendation: str,
) -> None:
    """End-to-end: the composer's output flows into the existing
    renderer. If the renderer's signature contract changes, this
    test catches it."""
    composite = compose_from_cycle(store, cycle_with_recommendation)
    report_md = render_report(composite)
    trace_json = render_trace(composite)
    assert isinstance(report_md, str)
    assert len(report_md) > 0
    assert isinstance(trace_json, str)
    assert len(trace_json) > 0
