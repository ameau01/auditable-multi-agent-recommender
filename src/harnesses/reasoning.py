"""Reasoning Harness — pre-emit checks on specialist findings and the
evaluator record.

What it checks today (first implementation):

  - `check_finding_type`     — finding_type is one of the three-valued
    {issue_found, no_issue_found, insufficient_data, diagnostic_deferral}
    set. The Composite Pydantic model already enforces this at parse
    time; this harness check exists so the verdict is explicit in the
    audit trail (the agent's structured output passed the three-valued
    rule) rather than implicit in "the Composite validated."
  - `check_evidence_refs_minimum` — if `finding_type == issue_found`,
    `evidence_refs` must be non-empty (otherwise the recommendation is a
    leap). Implements the evidence-sufficiency threshold from
    docs/harnesses.md §2.
  - `check_evaluator_drift_verdicts` — for an evaluator_record, every
    drift-check verdict must be one of {tight, loose, contradictory}.

What it will check in a later phase (declared, not implemented):

  - Full confidence-breakdown shape validation (every sub-signal named,
    every value in [0,1]).
  - Trade-off score completeness on evaluator records.

Each check writes a `harness_trail` row keyed by `check_name`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from ..audit.schema import audit_records
from ..audit.store import AuditStore
from ..models.audit import HarnessRecord
from ..models.enums import Verdict


# Valid finding_type values (mirrors models.enums.FindingType).
_VALID_FINDING_TYPES: frozenset[str] = frozenset({
    "issue_found",
    "no_issue_found",
    "insufficient_data",
    "diagnostic_deferral",
})

# Valid drift-check verdicts per docs/harnesses.md §2a Q1-Q3.
_VALID_DRIFT_VERDICTS: frozenset[str] = frozenset({
    "tight",
    "loose",
    "contradictory",
})


@dataclass
class ReasoningCheckResult:
    """Outcome of one reasoning-harness check."""
    passed: bool
    verdict: Verdict
    check_name: str
    harness_record_id: int
    failure_reason: str | None = None


class ReasoningHarness:
    """Pre-emit structured-output checks.

    These run BEFORE a specialist's finding or the evaluator's record
    is written to `audit_records`. A failing check should cause the
    caller to either retry the agent or surface the failure to the
    Supervisor; the harness itself only records the verdict.
    """

    def __init__(self, store: AuditStore) -> None:
        self._store = store

    # ----------------------------------------------------------------
    # Three-valued finding_type
    # ----------------------------------------------------------------
    def check_finding_type(
        self,
        cycle_id: str,
        finding_payload: dict[str, Any],
        related_event_id: int | None = None,
    ) -> ReasoningCheckResult:
        """Confirm `finding_payload['finding_type']` is in the four-valued
        set. (The third element 'diagnostic_deferral' is reserved for
        scenarios where the right answer is to defer.)
        """
        check_name = "finding_type_three_valued"
        finding_type = finding_payload.get("finding_type")
        if finding_type in _VALID_FINDING_TYPES:
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type="specialist_finding",
                related_event_id=related_event_id,
                verdict="passed",
                details={"finding_type": finding_type},
                failure_reason=None,
            )
        return self.route(
            cycle_id=cycle_id,
            check_name=check_name,
            target_event_type="specialist_finding",
            related_event_id=related_event_id,
            verdict="rejected",
            details={"finding_type": finding_type},
            failure_reason=(
                f"finding_type {finding_type!r} is not one of the "
                f"valid values: {sorted(_VALID_FINDING_TYPES)}."
            ),
        )

    # ----------------------------------------------------------------
    # Evidence-sufficiency threshold
    # ----------------------------------------------------------------
    def check_evidence_refs_minimum(
        self,
        cycle_id: str,
        finding_payload: dict[str, Any],
        minimum: int = 1,
        related_event_id: int | None = None,
    ) -> ReasoningCheckResult:
        """When `finding_type == issue_found`, require at least
        `minimum` entries in `evidence_refs`. Findings with non-issue
        types (no_issue_found, insufficient_data, diagnostic_deferral)
        are not subject to this check — they record an absence and
        the Composite short-circuits scoring.
        """
        check_name = "evidence_refs_minimum"
        finding_type = finding_payload.get("finding_type")
        evidence_refs = finding_payload.get("evidence_refs") or []

        # Only issue_found findings need to meet the threshold.
        if finding_type != "issue_found":
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type="specialist_finding",
                related_event_id=related_event_id,
                verdict="passed",
                details={
                    "finding_type": finding_type,
                    "evidence_refs_count": len(evidence_refs),
                    "minimum_required": minimum,
                    "applies": False,
                },
                failure_reason=None,
            )

        if len(evidence_refs) >= minimum:
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type="specialist_finding",
                related_event_id=related_event_id,
                verdict="passed",
                details={
                    "finding_type": finding_type,
                    "evidence_refs_count": len(evidence_refs),
                    "minimum_required": minimum,
                },
                failure_reason=None,
            )
        return self.route(
            cycle_id=cycle_id,
            check_name=check_name,
            target_event_type="specialist_finding",
            related_event_id=related_event_id,
            verdict="rejected",
            details={
                "finding_type": finding_type,
                "evidence_refs_count": len(evidence_refs),
                "minimum_required": minimum,
            },
            failure_reason=(
                f"finding_type=issue_found requires evidence_refs of "
                f"length >= {minimum}; got {len(evidence_refs)}."
            ),
        )

    # ----------------------------------------------------------------
    # Evaluator drift-check verdicts
    # ----------------------------------------------------------------
    def check_evaluator_drift_verdicts(
        self,
        cycle_id: str,
        evaluator_payload: dict[str, Any],
        related_event_id: int | None = None,
    ) -> ReasoningCheckResult:
        """Each per-specialist drift-check verdict on an evaluator record
        must be one of {tight, loose, contradictory}. Reads the verdicts
        from `evaluator_payload['drift_verdicts']`, which is expected to
        be a dict of {specialist_name: verdict_string}.
        """
        check_name = "evaluator_drift_verdicts_valid"
        drift = evaluator_payload.get("drift_verdicts") or {}
        invalid: list[tuple[str, str]] = [
            (k, v) for k, v in drift.items()
            if v not in _VALID_DRIFT_VERDICTS
        ]
        if not invalid:
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type="evaluator_record",
                related_event_id=related_event_id,
                verdict="passed",
                details={"verdict_count": len(drift)},
                failure_reason=None,
            )
        return self.route(
            cycle_id=cycle_id,
            check_name=check_name,
            target_event_type="evaluator_record",
            related_event_id=related_event_id,
            verdict="rejected",
            details={
                "verdict_count": len(drift),
                "invalid_verdicts": invalid,
            },
            failure_reason=(
                f"{len(invalid)} drift verdict(s) outside the valid "
                f"set {sorted(_VALID_DRIFT_VERDICTS)}: {invalid}."
            ),
        )

    # ----------------------------------------------------------------
    # Decision evidence-backing (Supervisor + future routers)
    # ----------------------------------------------------------------
    def check_decision_evidence_backed(
        self,
        cycle_id: str,
        decision_payload: dict[str, Any],
        related_event_id: int | None = None,
        record_type: str = "supervisor_decision",
    ) -> ReasoningCheckResult:
        """Confirm every decision is backed by evidence the cycle owns.

        Three rejection categories:
          - missing  : evidence_refs absent or empty.
          - dangling : a cited id has no audit_records row at all.
          - foreign  : a cited id exists but belongs to a different cycle.

        On pass: writes a `passed` reasoning_check row and returns
        ReasoningCheckResult(passed=True). The caller (typically the
        Supervisor) should treat a failed check as a routing block — the
        decision must not be acted on.

        Design rationale: the audit trail's "every claim traces to an
        observation" property requires every routing decision to cite
        the evidence it relied on. This check enforces that property at
        decision time, not gate time — so a decision that lacks evidence
        never gets to act.
        """
        check_name = "decision_evidence_backed"
        # The harness_trail row records the audit RecordType the decision
        # will be written as ("supervisor_decision" vs
        # "system_mapper_output"). The caller passes it explicitly so a
        # reader of harness_trail can distinguish what was verified
        # without joining audit_records. The supervisor's *decision_type*
        # ("dispatch_system_mapper" / "complete") is a sub-categorization
        # of supervisor_decision and stays in audit_records.content.
        target_event_type = record_type
        evidence_refs = decision_payload.get("evidence_refs") or []

        # Category 1: missing.
        if not evidence_refs:
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type=target_event_type,
                related_event_id=related_event_id,
                verdict="rejected",
                details={
                    "decision_type": decision_payload.get("decision_type"),
                    "evidence_refs": [],
                    "rejection_category": "missing",
                },
                failure_reason=(
                    "decision has no evidence_refs; every routing decision "
                    "must cite at least one audit_records id it relied on."
                ),
            )

        # Resolve all cited ids in one query and bucket by category.
        with self._store.engine.connect() as conn:
            rows = conn.execute(
                select(audit_records.c.id, audit_records.c.cycle_id)
                .where(audit_records.c.id.in_(evidence_refs))
            ).fetchall()
        present: dict[int, str] = {int(r[0]): r[1] for r in rows}

        dangling = [rid for rid in evidence_refs if rid not in present]
        foreign = [
            rid for rid in evidence_refs
            if rid in present and present[rid] != cycle_id
        ]

        if dangling:
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type=target_event_type,
                related_event_id=related_event_id,
                verdict="rejected",
                details={
                    "decision_type": decision_payload.get("decision_type"),
                    "evidence_refs": list(evidence_refs),
                    "dangling_refs": dangling,
                    "rejection_category": "dangling",
                },
                failure_reason=(
                    f"{len(dangling)} evidence_ref(s) do not resolve to any "
                    f"audit_records row: {dangling}."
                ),
            )
        if foreign:
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type=target_event_type,
                related_event_id=related_event_id,
                verdict="rejected",
                details={
                    "decision_type": decision_payload.get("decision_type"),
                    "evidence_refs": list(evidence_refs),
                    "foreign_refs": foreign,
                    "rejection_category": "foreign",
                },
                failure_reason=(
                    f"{len(foreign)} evidence_ref(s) belong to a different "
                    f"cycle: {foreign}. Decisions can only cite evidence "
                    "from their own cycle."
                ),
            )

        return self.route(
            cycle_id=cycle_id,
            check_name=check_name,
            target_event_type=target_event_type,
            related_event_id=related_event_id,
            verdict="passed",
            details={
                "decision_type": decision_payload.get("decision_type"),
                "evidence_refs": list(evidence_refs),
                "verified_count": len(evidence_refs),
            },
            failure_reason=None,
        )

    # ----------------------------------------------------------------
    # Public: route a verdict through the harness
    # ----------------------------------------------------------------
    # Public on purpose. The Supervisor (and future callers) route their
    # decisions through this method so the decision lands as a
    # `harness_trail` row with a Reasoning Harness verdict attached.
    # "Route" matches the LangGraph vocabulary — moving a verdict from
    # one place to the next, as opposed to "emit" which in LangGraph
    # specifically denotes streaming Pregel events.
    def route(
        self,
        cycle_id: str,
        check_name: str,
        target_event_type: str,
        related_event_id: int | None,
        verdict: Verdict,
        details: dict[str, Any],
        failure_reason: str | None,
    ) -> ReasoningCheckResult:
        record = HarnessRecord(
            cycle_id=cycle_id,
            parent_id=None,
            related_event_id=related_event_id,
            harness="reasoning",
            type="reasoning_check",
            verdict=verdict,
            content={
                "check_name": check_name,
                "target_event_type": target_event_type,
                "details": details,
                "failure_reason": failure_reason,
            },
        )
        rid = self._store.add_harness_event(record)
        return ReasoningCheckResult(
            passed=(verdict == "passed"),
            verdict=verdict,
            check_name=check_name,
            harness_record_id=rid,
            failure_reason=failure_reason,
        )
