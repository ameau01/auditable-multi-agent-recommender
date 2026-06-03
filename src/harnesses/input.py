"""Input Harness — validates the ingest bundle before any reasoning runs.

What it actually checks today (first implementation):

  - `validate_trigger`  — the review trigger is well-formed (the
    application_id matches the published-dataset shape `app-NN`).
  - `validate_application_known` — the application_id is one this system
    knows about (i.e. listed in the published dataset).

What it will check in a later phase (declared here for design honesty,
not yet implemented):

  - Schema conformance of telemetry tier arrays.
  - Record completeness (1,344 records per tier across 14 days).
  - Timestamp continuity (15-minute intervals, no gaps).
  - Cross-tier timestamp alignment.
  - Sidecar field presence for scenarios that need it (top_queries,
    per_instance_breakdown).
  - Terraform parseability.

The later checks are best implemented when the dataset is loaded — this
class accepts an optional `bundle` argument on `validate_bundle` that
returns NotImplementedError today so the surface is visible without the
implementation blocking the rest of the phase.

Every check writes a `harness_trail` row keyed by `check_name` and
returns a `ValidationResult` to the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..audit.store import AuditStore
from ..models.audit import HarnessRecord
from ..models.enums import Verdict


# Application ids in the published dataset are app-01 through app-18.
# This pattern is the well-formedness check; existence-in-dataset is a
# separate check that consults a known set.
_APP_ID_PATTERN = re.compile(r"^app-(0[1-9]|1[0-8])$")

# The 18 known applications. Mirrors what data_loader.list_app_names()
# would return, but kept as a local constant so the Input Harness has
# no runtime dependency on the dataset cache.
_KNOWN_APP_IDS: frozenset[str] = frozenset(
    f"app-{n:02d}" for n in range(1, 19)
)


@dataclass
class ValidationResult:
    """Return value from any Input Harness check.

    `passed` is the boolean shorthand callers reach for; `verdict`,
    `failure_reason`, and `harness_record_id` carry the full structured
    information for the caller that wants to surface it to the user.
    """
    passed: bool
    verdict: Verdict
    check_name: str
    harness_record_id: int
    failure_reason: str | None = None


class InputHarness:
    """Validates the ingest bundle. Writes one harness_trail row per check.

    Construct with an `AuditStore`. Reuse across cycles — the harness
    holds no per-cycle state.
    """

    def __init__(self, store: AuditStore) -> None:
        self._store = store

    # ----------------------------------------------------------------
    # Trigger checks (the only ones with real logic today)
    # ----------------------------------------------------------------
    def validate_trigger(
        self,
        cycle_id: str,
        application_id: str,
    ) -> ValidationResult:
        """Confirm the trigger's application_id is well-formed.

        Well-formed means it matches the `app-NN` shape where NN is 01-18.
        This is the trigger-legitimacy check from docs/harnesses.md §1.
        Stronger existence-in-dataset checking lives in
        `validate_application_known`.
        """
        check_name = "trigger_legitimacy"
        if _APP_ID_PATTERN.match(application_id):
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                application_id=application_id,
                verdict="passed",
                failure_reason=None,
            )
        return self.route(
            cycle_id=cycle_id,
            check_name=check_name,
            application_id=application_id,
            verdict="rejected",
            failure_reason=(
                f"application_id {application_id!r} is not a valid "
                "trigger; expected pattern 'app-NN' for N in 01..18."
            ),
        )

    def validate_application_known(
        self,
        cycle_id: str,
        application_id: str,
    ) -> ValidationResult:
        """Confirm the application_id is one this system knows about.

        Distinct from `validate_trigger` because a future scenario could
        be added to the dataset (extending the known set) without
        changing the well-formedness rule. Today both checks should
        return the same verdict for a given input; the split is
        intentional so each failure mode is distinguishable in the trail.
        """
        check_name = "application_known"
        if application_id in _KNOWN_APP_IDS:
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                application_id=application_id,
                verdict="passed",
                failure_reason=None,
            )
        return self.route(
            cycle_id=cycle_id,
            check_name=check_name,
            application_id=application_id,
            verdict="rejected",
            failure_reason=(
                f"application_id {application_id!r} is not a known "
                "scenario in the published dataset."
            ),
        )

    # ----------------------------------------------------------------
    # Bundle checks (declared, not implemented in this phase)
    # ----------------------------------------------------------------
    def validate_bundle(
        self,
        cycle_id: str,
        bundle: dict[str, Any],
    ) -> ValidationResult:
        """Full schema/completeness/continuity check over the loaded bundle.

        Implementation is deferred to the next phase, when the bundle
        format is finalized end-to-end. The signature is fixed so
        callers can wire the harness in now and the body lands later.
        """
        raise NotImplementedError(
            "Bundle-level validation lands in the next phase. The "
            "trigger checks above are sufficient for the current "
            "wiring; see docs/harnesses.md §1 for the full check list."
        )

    # ----------------------------------------------------------------
    # Public: route the validation verdict and return the result
    # ----------------------------------------------------------------
    # Public on purpose, matching ReasoningHarness.route and
    # ActionHarness.route_policy_check. Callers route a verdict through
    # this method so it lands as a harness_trail row with the
    # appropriate enforcement type. "Route" matches LangGraph vocabulary
    # — moving a verdict from one node to the next — rather than "emit,"
    # which in LangGraph specifically denotes streaming Pregel events.
    def route(
        self,
        cycle_id: str,
        check_name: str,
        application_id: str,
        verdict: Verdict,
        failure_reason: str | None,
    ) -> ValidationResult:
        record = HarnessRecord(
            cycle_id=cycle_id,
            parent_id=None,
            related_event_id=None,
            harness="input",
            type="input_validation",
            verdict=verdict,
            content={
                "check_name": check_name,
                "application_id": application_id,
                "details": {},
                "failure_reason": failure_reason,
            },
        )
        rid = self._store.add_harness_event(record)
        return ValidationResult(
            passed=(verdict == "passed"),
            verdict=verdict,
            check_name=check_name,
            harness_record_id=rid,
            failure_reason=failure_reason,
        )
