"""Orchestration Harness — validates cycle-level transitions.

Where the other three harnesses gate fine-grained events (one tool call,
one structured output, one trigger validation), the Orchestration
Harness gates *cycle-level* transitions: events that no single agent
owns because they are about the cycle's overall shape rather than any
one agent's decision.

In Phase 11a.4 there is exactly one check:

  - `check_cycle_completion_legitimate` — fires immediately before the
    `cycle_completed` row is written. Confirms the terminal_state the
    runner is about to record is consistent with what actually happened
    in the cycle. Three rejection categories:

      1. terminal_state == 'completed' but no specialists were invoked.
         A cycle that completes without doing any specialist work is
         not legitimately 'completed' — it should be 'no_specialists'.
      2. terminal_state == 'failed' but `failed_at_stage` is None.
         Every failure must name its stage so the renderer and the
         eval scripts can branch on it.
      3. terminal_state == 'rejected_input' but `failed_at_stage` is
         not 'input_harness'. The Input Harness is the only thing
         that can reject the trigger; any other stage producing this
         label is a bug.

Phase 11b+ will add more checks here as the orchestration story grows
(validate_specialists_completed before the Evaluator runs,
should_proceed_to_evaluator at the synthesize decision, etc).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..audit.store import AuditStore
from ..models.audit import HarnessRecord
from ..models.enums import Verdict


# Terminal states that flow through the cycle_complete node. Mirrors
# CycleState.terminal_state. Kept here as a local frozenset rather than
# a new Literal because the orchestration harness is the only consumer.
_TERMINAL_COMPLETED: str = "completed"
_TERMINAL_NO_SPECIALISTS: str = "no_specialists"
_TERMINAL_FAILED: str = "failed"
_TERMINAL_REJECTED_INPUT: str = "rejected_input"


@dataclass
class OrchestrationCheckResult:
    """Outcome of one orchestration-harness check.

    `passed` is True when the verdict is 'passed'; the caller treats a
    rejected verdict as a routing block. `harness_record_id` is the
    harness_trail row id so the caller can later link it to an audit
    row via UPDATE (parallel to dispatch.py's backfill pattern for
    action checks).
    """
    passed: bool
    verdict: Verdict
    check_name: str
    harness_record_id: int
    failure_reason: str | None = None


class OrchestrationHarness:
    """Cycle-level transition checks.

    Constructor takes the audit store so harness verdicts can be
    written to `harness_trail`. Stateless across cycles — one instance
    can serve every cycle in a process.
    """

    def __init__(self, store: AuditStore) -> None:
        self._store = store

    # ----------------------------------------------------------------
    # check_cycle_completion_legitimate
    # ----------------------------------------------------------------
    def check_cycle_completion_legitimate(
        self,
        cycle_id: str,
        final_status: str,
        failed_at_stage: str | None,
        specialists_invoked: list[str],
        related_event_id: int | None = None,
    ) -> OrchestrationCheckResult:
        """Validate the cycle's terminal_state is consistent with what
        actually happened. Fires before the runner writes the
        `cycle_completed` row.

        See module docstring for the three rejection categories. On
        pass: writes an `orchestration_check` row with verdict='passed'
        and returns OrchestrationCheckResult(passed=True). On rejection:
        writes verdict='rejected' and returns passed=False with a
        failure_reason; the caller (the cycle_complete graph node)
        should treat this as a routing-level error.
        """
        check_name = "cycle_completion_legitimate"
        target_event_type = "cycle_completed"
        details = {
            "final_status": final_status,
            "failed_at_stage": failed_at_stage,
            "specialists_invoked": list(specialists_invoked),
        }

        # Category 1: completed without specialists.
        if (final_status == _TERMINAL_COMPLETED
                and not specialists_invoked):
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type=target_event_type,
                related_event_id=related_event_id,
                verdict="rejected",
                details=details,
                failure_reason=(
                    "terminal_state 'completed' but no specialists "
                    "were invoked; use 'no_specialists' instead."
                ),
            )

        # Category 2: failed without a stage.
        if final_status == _TERMINAL_FAILED and failed_at_stage is None:
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type=target_event_type,
                related_event_id=related_event_id,
                verdict="rejected",
                details=details,
                failure_reason=(
                    "terminal_state 'failed' must carry a "
                    "failed_at_stage; got None."
                ),
            )

        # Category 3: rejected_input with the wrong stage.
        if (final_status == _TERMINAL_REJECTED_INPUT
                and failed_at_stage != "input_harness"):
            return self.route(
                cycle_id=cycle_id,
                check_name=check_name,
                target_event_type=target_event_type,
                related_event_id=related_event_id,
                verdict="rejected",
                details=details,
                failure_reason=(
                    f"terminal_state 'rejected_input' must be stamped "
                    f"failed_at_stage='input_harness'; got "
                    f"{failed_at_stage!r}."
                ),
            )

        # All checks passed.
        return self.route(
            cycle_id=cycle_id,
            check_name=check_name,
            target_event_type=target_event_type,
            related_event_id=related_event_id,
            verdict="passed",
            details=details,
            failure_reason=None,
        )

    # ----------------------------------------------------------------
    # route — public for symmetry with the other harnesses
    # ----------------------------------------------------------------
    def route(
        self,
        cycle_id: str,
        check_name: str,
        target_event_type: str,
        related_event_id: int | None,
        verdict: Verdict,
        details: dict[str, Any],
        failure_reason: str | None,
    ) -> OrchestrationCheckResult:
        """Write one `orchestration_check` row to harness_trail. Public
        so test code can drive it directly, matching the route() shape
        on InputHarness / ActionHarness / ReasoningHarness.
        """
        record = HarnessRecord(
            cycle_id=cycle_id,
            parent_id=None,
            related_event_id=related_event_id,
            harness="orchestration",
            type="orchestration_check",
            verdict=verdict,
            content={
                "check_name": check_name,
                "target_event_type": target_event_type,
                "details": details,
                "failure_reason": failure_reason,
            },
        )
        rid = self._store.add_harness_event(record)
        return OrchestrationCheckResult(
            passed=(verdict == "passed"),
            verdict=verdict,
            check_name=check_name,
            harness_record_id=rid,
            failure_reason=failure_reason,
        )
