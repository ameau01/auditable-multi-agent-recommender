"""Reconstruct a Composite from a cycle's records.

The audit store records every reasoning event as an audit_record. The
renderer consumes a Composite (with a populated TraceSection). The
composer bridges the two — it reads the cycle's records and assembles
a fully-populated Composite that the renderer can hand to its existing
render_report and render_trace functions unchanged.

This is what "the audit trail is the source of truth for the trace
section" means in practice: the trace_section field is NEVER written
into the recommendation record; it is always derived from the cycle's
audit_records at read time, via this composer.
"""

from __future__ import annotations

from typing import Any

from ..models.composite import Composite, TraceSection
from .queries import find_recommendation_for_cycle, get_cycle_events
from .store import AuditStore


# Map from record `type` to the TraceSection field that should hold
# a summary of that record's content. Most fields hold a single
# decision-summary dict (most-recent-event of that type); specialist
# findings is a list because there are typically three (one per tier
# specialist).
_TRACE_FIELD_BY_TYPE: dict[str, str] = {
    "review_request": "review",
    "supervisor_decision": "supervisor_decision",
    "evaluator_record": "evaluator_records",
    "gate_verdict": "action_harness_gate",
    "hitl_decision": "hitl_decision",
}


def compose_from_cycle(store: AuditStore, cycle_id: str) -> Composite:
    """Reconstruct a Composite from the cycle's audit records.

    Process:
      1. Locate the cycle's recommendation record. Raise if absent.
      2. Hydrate the recommendation's content.composite into a Composite.
      3. Walk all cycle events and populate TraceSection sub-fields:
         - review                from review_request event content
         - supervisor_decision   from the latest supervisor_decision event
         - specialist_findings   from all specialist_finding events
         - evaluator_records     from the latest evaluator_record event
         - action_harness_gate   from the gate_verdict event (if present)
         - hitl_decision         from the hitl_decision event (if present)
      4. Return the augmented Composite.

    Raises:
      ValueError: if no recommendation record exists for the cycle.
    """
    rec = find_recommendation_for_cycle(store, cycle_id)
    if rec is None:
        raise ValueError(
            f"No recommendation record found for cycle_id={cycle_id!r}; "
            f"cannot compose Composite."
        )

    # Hydrate the composite stored in the recommendation event's content.
    composite_data = rec.content.get("composite") or {}
    composite = Composite.model_validate(composite_data)

    # Build the TraceSection from the cycle's records.
    events = get_cycle_events(store, cycle_id)
    trace = _build_trace_section(events)

    # Attach (Pydantic models are immutable by default, so use model_copy).
    return composite.model_copy(update={"trace": trace})


# ============================================================
# Internal: aggregate events into a TraceSection
# ============================================================
def _build_trace_section(events: list) -> TraceSection:
    """Collect cycle events into a TraceSection. Most fields capture
    the latest event of that type; specialist_findings is a list across
    all specialist contributions."""
    by_type: dict[str, list[Any]] = {}
    for ev in events:
        by_type.setdefault(ev.type, []).append(ev)

    section_data: dict[str, Any] = {}

    # Single-event fields: take the most-recent event's content
    for record_type, trace_field in _TRACE_FIELD_BY_TYPE.items():
        if record_type in by_type:
            section_data[trace_field] = by_type[record_type][-1].content

    # specialist_findings: list of all specialist_finding event contents
    if "specialist_finding" in by_type:
        section_data["specialist_findings"] = [
            ev.content for ev in by_type["specialist_finding"]
        ]

    # input_harness_validation slot reserved for the future input harness
    # gate_verdict / hitl_decision slots populated by future harness emitters

    return TraceSection.model_validate(section_data)
