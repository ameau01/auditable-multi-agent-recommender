"""Public entry point for running one cycle.

    from src.agents.runner import run_cycle
    cycle_id = run_cycle("app-08")

Responsibilities:

  1. Load .env so LangSmith env vars (`LANGCHAIN_TRACING_V2`,
     `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT`)
     are picked up by langchain's auto-instrumentation.
  2. Open the audit store, build the harnesses.
  3. `start_cycle` writes the cycle_started row and returns a new
     cycle_id.
  4. Build the LangGraph application and invoke it with an initial
     CycleState.
  5. `complete_cycle` writes the cycle_completed row regardless of
     how the graph terminated. This pair brackets the run so the
     audit trail's begin/end tags are always in place even when a
     node raises.

The store, harnesses, and audit DB path are injectable for tests.
Production callers just call `run_cycle(application_id)` and let the
defaults take care of everything.
"""

from __future__ import annotations

import os
from typing import Any

from ..audit.store import AuditStore
from ..common.init import ensure_env_loaded, get_audit_store
from ..harnesses.action import ActionHarness
from ..harnesses.input import InputHarness
from .orchestrator import build_graph
from .state import CycleState


# Trigger types we accept on `run_cycle`. The string lands in the
# cycle_started audit row's content.trigger_type.
TriggerType = str  # "manual" | "scheduled" | "test"


def run_cycle(
    application_id: str,
    *,
    trigger_type: TriggerType = "manual",
    notes: str | None = None,
    db_path: str | None = None,
    store: AuditStore | None = None,
) -> str:
    """Run one full cycle on `application_id`. Returns the cycle_id.

    Args:
        application_id: the application to review (e.g. "app-08").
        trigger_type:    "manual" | "scheduled" | "test". Lands in
                         the cycle_started audit row.
        notes:           optional free-text note for the cycle_started row.
        db_path:         override the audit DB path. None uses
                         AUDIT_DB_PATH from env or the default
                         (`.audit_db/audit.db`).
        store:           inject a pre-built AuditStore (typically for
                         tests using `:memory:`). When provided, db_path
                         is ignored.

    Returns:
        The cycle_id (e.g. "cycle_20260602_120304_a3f8b1c0"). This is
        the primary key used by the inspector CLI and the audit-trail
        readers downstream.
    """
    # 1. Environment + LangSmith auto-instrumentation.
    # Idempotent: ensure_env_loaded() touches .env at most once per
    # process. Must happen before any langchain import that reads
    # the LANGCHAIN_* / LANGSMITH_* env vars.
    ensure_env_loaded()

    # 2. Store + harnesses.
    if store is None:
        store = get_audit_store(db_path=db_path)
    input_harness = InputHarness(store)
    action_harness = ActionHarness(store)

    # 3. Start the cycle (writes cycle_started, returns cycle_id).
    cycle_id = store.start_cycle(
        application_id=application_id,
        trigger_type=trigger_type,
        notes=notes,
    )
    # Look up the cycle_started row id so Supervisor can cite it as
    # evidence on its first decision (before any other audit_records
    # row exists).
    cycle_started_id = store.get_cycle_started_id(cycle_id)

    # 4. Build graph and invoke.
    graph_app = build_graph(store, input_harness, action_harness)
    initial_state = CycleState(
        application_id=application_id,
        cycle_id=cycle_id,
        cycle_started_id=cycle_started_id,
    )

    # LangGraph's invoke returns the final state. Wrap in try/finally
    # so cycle_completed always writes — even if the graph itself
    # raises an uncaught exception.
    final_state: dict[str, Any] | None = None
    failure: str | None = None
    try:
        result = graph_app.invoke(
            initial_state,
            # Pass cycle_id as run metadata so LangSmith traces are
            # searchable by audit-trail id.
            config={"metadata": {"cycle_id": cycle_id, "application_id": application_id}},
        )
        # LangGraph returns either a CycleState or a dict-shaped state
        # depending on version. Normalize.
        if hasattr(result, "model_dump"):
            final_state = result.model_dump()
        else:
            final_state = dict(result)
    except Exception as exc:  # noqa: BLE001
        failure = f"{type(exc).__name__}: {exc}"

    # 5. Complete the cycle. `failed_at_stage` is the machine-readable
    #    counterpart to failure_reason — the cycle-complete node sets it
    #    when the graph terminates with a known stage; the outer except
    #    branch can't know the stage (uncaught exception), so it stays
    #    None there and the renderer can interpret that as "unknown /
    #    crashed".
    if failure is not None:
        store.complete_cycle(
            cycle_id=cycle_id,
            final_status="failed",
            failure_reason=failure,
            failed_at_stage=None,
        )
    else:
        assert final_state is not None
        terminal = final_state.get("terminal_state") or "completed"
        reason = final_state.get("failure_reason")
        stage = final_state.get("failed_at_stage")
        store.complete_cycle(
            cycle_id=cycle_id,
            final_status=terminal,
            failure_reason=reason,
            failed_at_stage=stage,
        )

    return cycle_id


def langsmith_enabled() -> bool:
    """True when the LangSmith env vars are set. Used by the runner's
    log line so the user can tell whether a trace will appear in the
    LangSmith UI without needing to inspect env directly."""
    return (
        os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in ("true", "1")
        and bool(os.environ.get("LANGSMITH_API_KEY"))
    )
