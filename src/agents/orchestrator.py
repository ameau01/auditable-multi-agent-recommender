"""LangGraph orchestrator: wires the cycle as a state machine.

The graph in Phase 11a:

    START
      ↓
    input_validation_gate
      ↓ (passed)                 ↓ (rejected)
    system_mapper                cycle_complete(rejected_input)
      ↓
    supervisor
      ↓
    cycle_complete(completed | no_specialists)
      ↓
    END

Phases 11b–11d add specialist nodes and the cross-tier evaluator
between supervisor and cycle_complete; the graph builder grows but
the agent wiring above remains intact.

Public API:
  - `build_graph(...)` returns a compiled LangGraph application.
  - `orchestrate(scenario)` retains the original stub signature for
    callers built before the runner landed; it now delegates to the
    public runner.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ..audit.store import AuditStore
from ..harnesses.action import ActionHarness
from ..harnesses.input import InputHarness
from ..harnesses.orchestration import OrchestrationHarness
from ..harnesses.reasoning import ReasoningHarness
from ..models.audit import AuditRecord
from .state import CycleState
from .supervisor import SupervisorError, SupervisorNode
from .system_mapper import SystemMapperError, SystemMapperNode


# ============================================================
# Node functions (closures over store + harnesses)
# ============================================================
def _make_input_validation_node(
    input_harness: InputHarness,
):
    """Validates the trigger via the Input Harness. Sets
    state.input_validation_passed; on rejection, sets terminal_state
    so the conditional edge routes to cycle_complete early.

    The audit store is not a parameter here because the Input Harness
    owns the trail writes via `add_harness_event` internally.
    """
    def node(state: CycleState) -> dict[str, Any]:
        trigger = input_harness.validate_trigger(
            cycle_id=state.cycle_id,
            application_id=state.application_id,
        )
        known = input_harness.validate_application_known(
            cycle_id=state.cycle_id,
            application_id=state.application_id,
        )
        passed = trigger.passed and known.passed
        # The Supervisor cites this row as evidence on its first
        # decision (dispatch_system_mapper). Use the application_known
        # row id (the second check, more specific).
        last_input_id = known.harness_record_id
        if passed:
            return {
                "input_validation_passed": True,
                "last_input_validation_record_id": last_input_id,
            }
        reason = trigger.failure_reason or known.failure_reason
        return {
            "input_validation_passed": False,
            "input_validation_reason": reason,
            "last_input_validation_record_id": last_input_id,
            "terminal_state": "rejected_input",
            "failure_reason": reason,
            "failed_at_stage": "input_harness",
        }
    return node


def _make_system_mapper_node(
    store: AuditStore,
    action_harness: ActionHarness,
    reasoning_harness: ReasoningHarness,
):
    """Wraps SystemMapperNode so System Mapper errors land as
    terminal_state='failed' rather than crashing the graph."""
    mapper = SystemMapperNode(store, action_harness, reasoning_harness)
    def node(state: CycleState) -> dict[str, Any]:
        try:
            return mapper.run(state)
        except SystemMapperError as exc:
            return {
                "terminal_state": "failed",
                "failure_reason": f"system_mapper: {exc}",
                "failed_at_stage": "system_mapper",
            }
    return node


def _make_supervisor_node(
    store: AuditStore,
    reasoning_harness: ReasoningHarness,
):
    """Wraps SupervisorNode with the same error-to-state mapping."""
    supervisor = SupervisorNode(store, reasoning_harness)
    def node(state: CycleState) -> dict[str, Any]:
        try:
            return supervisor.run(state)
        except SupervisorError as exc:
            return {
                "terminal_state": "failed",
                "failure_reason": f"supervisor: {exc}",
                "failed_at_stage": "supervisor",
            }
    return node


def _make_cycle_complete_node(orchestration_harness: OrchestrationHarness):
    """Decide the final terminal_state and route it through the
    Orchestration Harness's `cycle_completion_legitimate` check before
    the runner writes `cycle_completed`.

    Note: this node does NOT call AuditStore.complete_cycle. The
    runner owns the start/complete pair so the call brackets the
    graph execution; this means cycle_completed lands even if a
    node raises an uncaught exception inside the graph.

    On orchestration-check rejection: the harness has already written
    the rejected verdict to harness_trail, and we coerce the
    terminal_state to 'failed' with stage='orchestration' so the
    `cycle_completed` row the runner writes reflects the harness's
    refusal. This makes the rejection visible in both the audit trail
    (substance: the failed completion) and the harness trail (the
    enforcement: which rule was violated).
    """
    def node(state: CycleState) -> dict[str, Any]:
        if state.terminal_state in ("rejected_input", "failed"):
            final = state.terminal_state
            reason = state.failure_reason
            # If a node already set failed_at_stage (e.g. system_mapper on
            # a parse failure), respect it. Otherwise default based on the
            # terminal state: rejected_input -> input_harness; failed
            # without an explicit stage is left None for the runner's
            # outer try/except to fill in.
            stage = state.failed_at_stage
            if stage is None and final == "rejected_input":
                stage = "input_harness"
        elif not state.specialists_invoked:
            # Phase 11a: skeleton run, no specialists invoked. Not a
            # failure — supervisor decided there was nothing to dispatch.
            final = "no_specialists"
            reason = "Phase 11a: no specialists wired yet."
            stage = None
        else:
            final = "completed"
            reason = None
            stage = None

        # Cycle-level legitimacy check via the Orchestration Harness.
        check = orchestration_harness.check_cycle_completion_legitimate(
            cycle_id=state.cycle_id,
            final_status=final,
            failed_at_stage=stage,
            specialists_invoked=list(state.specialists_invoked),
            related_event_id=None,
        )
        if not check.passed:
            final = "failed"
            reason = f"orchestration_harness: {check.failure_reason}"
            stage = "orchestration"

        return {
            "terminal_state": final,
            "failure_reason": reason,
            "failed_at_stage": stage,
        }
    return node


# ============================================================
# Conditional edges — Supervisor is the only real router
# ============================================================
def _after_input_validation(state: CycleState) -> str:
    """On pass, hand off to Supervisor (the only router); on rejection,
    short-circuit to cycle_complete (the Input Harness has already set
    terminal_state='rejected_input' and failed_at_stage='input_harness').
    """
    return "supervisor" if state.input_validation_passed else "cycle_complete"


def _after_system_mapper(state: CycleState) -> str:
    """System Mapper always returns to Supervisor — workers never decide
    termination themselves. On failure the system_mapper node sets
    terminal_state='failed' (caught in the wrapper); the edge still
    points to Supervisor, but Supervisor's _decide handles the terminated
    case (analysis_plan is None) by raising SupervisorError, which the
    wrapper converts to cycle_complete with failed_at_stage='supervisor'
    — except when the mapper itself failed, we want failed_at_stage to
    stay 'system_mapper'. Short-circuit there to preserve the stage.
    """
    if state.terminal_state == "failed":
        return "cycle_complete"
    return "supervisor"


def _after_supervisor(state: CycleState) -> str:
    """Supervisor is the only router. Map its decision_type (cached on
    state.next_route by SupervisorNode.run) to a worker node or to
    cycle_complete. If Supervisor itself failed, terminal_state is set
    and we route to cycle_complete.
    """
    if state.terminal_state == "failed":
        return "cycle_complete"
    route = state.next_route
    if route == "dispatch_system_mapper":
        return "system_mapper"
    # Phase 11a: only the "complete" branch besides dispatch_system_mapper
    # is implemented. Phase 11b+ adds dispatch_specialists / synthesize / gate.
    return "cycle_complete"


# ============================================================
# Public builder
# ============================================================
def build_graph(
    store: AuditStore,
    input_harness: InputHarness,
    action_harness: ActionHarness,
    reasoning_harness: ReasoningHarness | None = None,
    orchestration_harness: OrchestrationHarness | None = None,
):
    """Build and compile the LangGraph application for one cycle.

    The returned app is a compiled graph whose `.invoke(initial_state)`
    drives the cycle. Construct fresh per cycle; LangGraph's state is
    per-invocation, not per-graph.

    `reasoning_harness` and `orchestration_harness` default to fresh
    instances bound to the same `store` — callers that hold long-lived
    harnesses can pass them in, but for normal use the defaults are
    right.
    """
    if reasoning_harness is None:
        reasoning_harness = ReasoningHarness(store)
    if orchestration_harness is None:
        orchestration_harness = OrchestrationHarness(store)

    graph: StateGraph = StateGraph(CycleState)

    graph.add_node(
        "input_validation",
        _make_input_validation_node(input_harness),
    )
    graph.add_node(
        "system_mapper",
        _make_system_mapper_node(store, action_harness, reasoning_harness),
    )
    graph.add_node(
        "supervisor",
        _make_supervisor_node(store, reasoning_harness),
    )
    graph.add_node(
        "cycle_complete",
        _make_cycle_complete_node(orchestration_harness),
    )

    graph.add_edge(START, "input_validation")
    # Input validation routes to Supervisor on pass; cycle_complete on
    # rejection. Supervisor is the only node that fans out to workers.
    graph.add_conditional_edges(
        "input_validation",
        _after_input_validation,
        {"supervisor": "supervisor", "cycle_complete": "cycle_complete"},
    )
    # Supervisor → {system_mapper, cycle_complete} (Phase 11a). Phase 11b
    # adds specialist_* targets here.
    graph.add_conditional_edges(
        "supervisor",
        _after_supervisor,
        {"system_mapper": "system_mapper", "cycle_complete": "cycle_complete"},
    )
    # System Mapper returns to Supervisor — workers don't decide
    # termination themselves.
    graph.add_conditional_edges(
        "system_mapper",
        _after_system_mapper,
        {"supervisor": "supervisor", "cycle_complete": "cycle_complete"},
    )
    graph.add_edge("cycle_complete", END)

    return graph.compile()


# ============================================================
# LangGraph Studio entry point
# ============================================================
def graph_factory():
    """Zero-arg factory that the LangGraph dev server / Studio can call.

    Uses an in-memory audit store on purpose:

      - Studio loads the factory from inside an ASGI loop, and blockbuster
        rejects sync filesystem I/O there (the on-disk store calls mkdir
        for `.audit_db/` during construction). In-memory has no FS I/O.
      - Studio runs are exploratory; persisting them to the real DB would
        pollute the trail the inspect CLI surfaces.

    For persistent cycles, use `scripts/run_agents.sh app-NN` — that uses
    the on-disk store. Studio is for graph visualization + ad-hoc runs.

    Referenced from `langgraph.json` at the repo root:
        {"graphs": {"agent": "src.agents.orchestrator:graph_factory"}}
    """
    from ..common.init import get_audit_store
    store = get_audit_store(db_path=":memory:")
    return build_graph(store, InputHarness(store), ActionHarness(store))


# ============================================================
# Backwards-compat stub (kept so any import of `orchestrate` doesn't
# break; the new entry point is `runner.run_cycle`)
# ============================================================
def orchestrate(scenario: dict[str, Any]) -> dict[str, Any]:
    """Legacy entry point retained for backward compatibility.

    The real entry point for Phase 11+ is `src.agents.runner.run_cycle`,
    which takes an application_id and returns a cycle_id. This function
    is preserved so older callers continue to import without an
    ImportError; it raises with a pointer to the new entry point.
    """
    raise NotImplementedError(
        "orchestrate(scenario) is the legacy stub. Use "
        "`from src.agents.runner import run_cycle; run_cycle(app_id)` "
        "instead. See CHANGELOG Phase 11a."
    )


# Suppress unused-import warning for AuditRecord (re-exported below for
# downstream notebooks that build CycleState manually).
__all__ = ["build_graph", "orchestrate", "AuditRecord"]
