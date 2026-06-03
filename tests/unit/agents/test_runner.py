"""Tests for the public runner entry point.

Covers:
  - run_cycle returns a cycle_id of the expected shape.
  - cycle_started + cycle_completed rows both land.
  - terminal_state propagates into the cycle_completed row.
  - Bogus app id flows to terminal_state='rejected_input'.
  - LangSmith env flag detector.

The runner needs the same mocked MCP as the orchestrator tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.runner import langsmith_enabled, run_cycle
from src.audit import AuditStore
from src.audit.queries import find_recommendation_for_cycle, get_cycle_events


def test_run_cycle_returns_well_formed_id(
    store: AuditStore,
    mock_mcp,
    app_08_metadata: dict[str, Any],
    app_08_terraform: dict[str, Any],
) -> None:
    mock_mcp.register("get_scenario_metadata", app_08_metadata)
    mock_mcp.register("get_terraform", app_08_terraform)

    cid = run_cycle("app-08", store=store, trigger_type="test")
    assert cid.startswith("cycle_")
    assert len(cid.split("_")) == 4


def test_run_cycle_writes_cycle_completed(
    store: AuditStore,
    mock_mcp,
    app_08_metadata: dict[str, Any],
    app_08_terraform: dict[str, Any],
) -> None:
    mock_mcp.register("get_scenario_metadata", app_08_metadata)
    mock_mcp.register("get_terraform", app_08_terraform)

    cid = run_cycle("app-08", store=store, trigger_type="test")
    events = get_cycle_events(store, cid)
    types = [e.type for e in events]
    assert types[0] == "cycle_started"
    assert types[-1] == "cycle_completed"


def test_terminal_state_propagates_into_cycle_completed_content(
    store: AuditStore,
    mock_mcp,
    app_08_metadata: dict[str, Any],
    app_08_terraform: dict[str, Any],
) -> None:
    """Phase 11a always ends in 'no_specialists' on a clean skeleton run.
    The cycle_completed row's content.final_status MUST reflect that —
    the runner's final_status is the user-visible verdict."""
    mock_mcp.register("get_scenario_metadata", app_08_metadata)
    mock_mcp.register("get_terraform", app_08_terraform)

    cid = run_cycle("app-08", store=store, trigger_type="test")
    events = get_cycle_events(store, cid)
    done = next(e for e in events if e.type == "cycle_completed")
    assert done.content["final_status"] == "no_specialists"


def test_run_cycle_with_bogus_app_terminates_rejected(
    store: AuditStore,
) -> None:
    """No mock needed — the Input Harness rejects before any MCP call."""
    cid = run_cycle("bogus_app", store=store, trigger_type="test")
    events = get_cycle_events(store, cid)
    done = next(e for e in events if e.type == "cycle_completed")
    assert done.content["final_status"] == "rejected_input"


def test_no_recommendation_for_skeleton_run(
    store: AuditStore,
    mock_mcp,
    app_08_metadata: dict[str, Any],
    app_08_terraform: dict[str, Any],
) -> None:
    """Phase 11a invokes zero specialists, so no recommendation row
    lands. `find_recommendation_for_cycle` returns None and
    `compose_from_cycle` would (correctly) raise."""
    mock_mcp.register("get_scenario_metadata", app_08_metadata)
    mock_mcp.register("get_terraform", app_08_terraform)
    cid = run_cycle("app-08", store=store, trigger_type="test")
    assert find_recommendation_for_cycle(store, cid) is None


# ============================================================
# LangSmith env flag detection
# ============================================================
def test_langsmith_disabled_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert langsmith_enabled() is False


def test_langsmith_enabled_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
    assert langsmith_enabled() is True


def test_langsmith_disabled_when_tracing_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
    assert langsmith_enabled() is False
