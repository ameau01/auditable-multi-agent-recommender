"""Shared fixtures for agent unit tests.

All agent unit tests use in-memory SQLite + a mocked MCP adapter so the
suite never touches the filesystem or the Hugging Face dataset.

Fixtures:
  - `store`           — fresh in-memory AuditStore with schema initialized
  - `input_harness`   — InputHarness bound to the store
  - `action_harness`  — ActionHarness bound to the store
  - `cycle_id`        — a started cycle (cycle_started row already written)
  - `mock_mcp`        — monkeypatches mcp_adapter.call_tool to return canned
                        responses keyed by tool_name. Tests register tools they
                        expect to be called via `mock_mcp.register(...)`.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from src.agents import mcp_adapter
from src.audit import AuditStore
from src.audit.store import IN_MEMORY
from src.harnesses.action import ActionHarness
from src.harnesses.input import InputHarness


@pytest.fixture
def store() -> AuditStore:
    s = AuditStore(db_path=IN_MEMORY)
    s.initialize()
    return s


@pytest.fixture
def input_harness(store: AuditStore) -> InputHarness:
    return InputHarness(store)


@pytest.fixture
def action_harness(store: AuditStore) -> ActionHarness:
    return ActionHarness(store)


@pytest.fixture
def cycle_id(store: AuditStore) -> str:
    return store.start_cycle(application_id="app-08", trigger_type="test")


class _MockMcp:
    """Replaces mcp_adapter.call_tool with a lookup table. Tests register
    responses per (tool_name) and inspect calls afterward.

    Usage:
        def test_thing(mock_mcp):
            mock_mcp.register("get_terraform", {"terraform": "..."})
            ...  # run code that calls dispatch / system_mapper
            assert mock_mcp.calls[0]["tool_name"] == "get_terraform"
    """

    def __init__(self) -> None:
        self._responses: dict[str, Any] = {}
        self._raises: dict[str, Exception] = {}
        self.calls: list[dict[str, Any]] = []

    def register(self, tool_name: str, response: dict[str, Any]) -> None:
        """Make `call_tool(tool_name, args)` return `response`."""
        self._responses[tool_name] = response

    def register_raise(self, tool_name: str, exc: Exception) -> None:
        """Make `call_tool(tool_name, args)` raise `exc`."""
        self._raises[tool_name] = exc

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        if tool_name in self._raises:
            raise self._raises[tool_name]
        if tool_name not in self._responses:
            raise KeyError(
                f"test did not register a response for {tool_name!r}; "
                f"registered: {sorted(self._responses)}"
            )
        return self._responses[tool_name]


@pytest.fixture
def mock_mcp(monkeypatch: pytest.MonkeyPatch) -> _MockMcp:
    """Replace mcp_adapter.call_tool with the mock for this test."""
    m = _MockMcp()
    monkeypatch.setattr(mcp_adapter, "call_tool", m.call_tool)
    return m


# Helpers reused across tests.
#
# Note the response envelopes — these match the real
# `GetScenarioMetadataResponse` / `GetTerraformResponse` shapes from
# src/models/telemetry.py. `get_scenario_metadata` returns
# `{app_name, metadata: {...}}` (nested), while `get_terraform`
# returns `{app_name, terraform}` (flat). Tests that bypass this
# envelope structure miss real wire-shape bugs.
APP_08_METADATA_FIXTURE: dict[str, Any] = {
    "app_name": "app-08",
    "metadata": {
        "scenario_id": "08",
        "scenario_name": "cross-tier database cascade",
        "scenario_type": "cross_tier_negative",
        "tier_topology": {
            "compute":  {"present": True, "instance_class": "m5.large"},
            "database": {"present": True, "instance_class": "db.r6g.xlarge"},
            "cache":    None,
            "network":  None,
        },
        "business_context": {},
    },
}

APP_08_TERRAFORM_FIXTURE: dict[str, Any] = {
    "app_name": "app-08",
    "terraform": (
        'resource "aws_launch_template" "compute" {}\n'
        'resource "aws_db_instance" "database_primary" {}\n'
    ),
}


@pytest.fixture
def app_08_metadata() -> dict[str, Any]:
    return dict(APP_08_METADATA_FIXTURE)


@pytest.fixture
def app_08_terraform() -> dict[str, Any]:
    return dict(APP_08_TERRAFORM_FIXTURE)


# Make sure pytest collects this folder
def pytest_collection_modifyitems(config: Callable, items: list) -> None:
    return None
