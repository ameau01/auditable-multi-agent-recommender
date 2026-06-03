"""LangGraph state schema — the contract every node reads and writes.

One Pydantic model that flows through the graph. Each node receives
the full state, may set fields it owns, and returns either the same
state or a partial update (LangGraph merges).

Field ownership:

  - `application_id`        : set by the runner at cycle start; read-only thereafter.
  - `cycle_id`              : set by the runner once AuditStore.start_cycle() returns it.
  - `input_validation_passed`: set by the input-harness gate node.
  - `analysis_plan`         : set by the System Mapper node.
  - `specialists_invoked`   : set by the Supervisor node. In Phase 11a this is always [].
  - `specialist_findings`   : set by tier specialists in Phase 11b+. Empty in 11a.
  - `evaluator_record`      : set by the Cross-Tier Evaluator in Phase 11d+. None in 11a.
  - `recommendation`        : set by the Cross-Tier Evaluator in Phase 11d+. None in 11a.
  - `terminal_state`        : set by the cycle-complete node. One of:
                                "completed"        — cycle ran clean to end
                                "rejected_input"   — Input Harness blocked at the gate
                                "no_specialists"   — Supervisor invoked zero specialists
                                "failed"           — uncaught exception in a node
  - `failure_reason`        : human-readable string when terminal_state != "completed".

The state is locked at this shape from 11a onward. Specialists in 11b
will append to `specialist_findings`. The Cross-Tier Evaluator in 11d
will set `evaluator_record` and `recommendation`. No node ever rewrites
a field that another node owns.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .analysis_plan import AnalysisPlan


class CycleState(BaseModel):
    """The LangGraph state. One instance per cycle."""

    # Set by the runner; immutable for the life of the cycle.
    application_id: str
    cycle_id: str
    # audit_records.id of the cycle_started row. The Supervisor cites
    # this as evidence on its very first decision (before any other
    # audit_records substance exists). Always set by the runner via
    # AuditStore.get_cycle_started_id after start_cycle inserts the row.
    cycle_started_id: int | None = None

    # Set by the input-harness gate node.
    input_validation_passed: bool = False
    input_validation_reason: str | None = None
    # audit_records.id of the input_harness summary the Supervisor can
    # cite as evidence on its first decision. None until input validation
    # has run; never None after the input gate has passed.
    last_input_validation_record_id: int | None = None

    # Set by the System Mapper.
    analysis_plan: AnalysisPlan | None = None
    # True once the System Mapper has produced a tier_topology that the
    # Supervisor can route on. Mirrors the truthiness of analysis_plan
    # but is named for the Supervisor's state-machine condition.
    has_system_map: bool = False
    # audit_records.id of the most recent system_mapper_output row.
    # Supervisor cites this in any decision that depends on the topology
    # (dispatch_specialists, complete-because-no-specialists, etc.).
    last_system_mapper_output_id: int | None = None

    # Set by the Supervisor.
    # `specialists_invoked` is the historical list of every specialist
    # that has been dispatched at least once. `specialists_to_invoke`
    # is the still-pending set the Supervisor decided to fan out to but
    # whose findings haven't landed yet. `specialists_completed` is the
    # set whose findings have landed. The Supervisor's state-machine
    # routes on the difference (to_invoke - completed).
    specialists_invoked: list[str] = Field(default_factory=list)
    specialists_to_invoke: list[str] = Field(default_factory=list)
    specialists_completed: list[str] = Field(default_factory=list)
    # audit_records.id of the Supervisor's most recent supervisor_decision
    # row. Lets the verifier walk every decision in the cycle by following
    # parent_id back from the latest one.
    last_supervisor_decision_id: int | None = None

    # Set by tier specialists (Phase 11b+). Empty in 11a.
    specialist_findings: list[dict[str, Any]] = Field(default_factory=list)
    # audit_records.id list of the specialist_finding rows landed this
    # cycle. The Supervisor cites these in its `synthesize` decision (the
    # Evaluator needs them as input).
    specialist_finding_record_ids: list[int] = Field(default_factory=list)

    # Set by the Cross-Tier Evaluator (Phase 11d+). None in 11a.
    evaluator_record: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    # audit_records.id of the most recent evaluator_record / recommendation
    # rows. Supervisor cites them in `gate` and `complete` decisions.
    last_evaluator_record_id: int | None = None
    last_recommendation_record_id: int | None = None

    # Supervisor's most recent routing decision. The conditional edge
    # after the supervisor node reads this to know which worker (or
    # cycle_complete) to route to next. Mirrors the SupervisorDecisionType
    # Literal in src/models/enums.py.
    next_route: str | None = None

    # Terminal state. Cycle-complete node sets this before END.
    terminal_state: str | None = None
    failure_reason: str | None = None
    # Machine-readable counterpart to failure_reason — names the stage
    # the cycle stopped at. Set whenever terminal_state != "completed";
    # one of FailureStage values (see src/models/enums.py). The runner
    # forwards this to AuditStore.complete_cycle so it lands on the
    # cycle_completed row's content.
    failed_at_stage: str | None = None

    model_config = ConfigDict(extra="forbid")
