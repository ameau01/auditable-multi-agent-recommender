"""Tests for the in-process MCP adapter.

Covers:
  - 18 tools registered (matches scope.py / wire-test expectation).
  - Unknown tool name raises KeyError.
  - Pydantic responses are model_dumped to plain dicts.

The adapter itself is thin — most checks verify the registry shape so
adding a new tool without wiring it here fails loud.
"""

from __future__ import annotations

import pytest

from src.agents import mcp_adapter
from src.mcp_server.scope import SPECIALIST_TOOL_ALLOWLIST


def test_known_tools_count_matches_wire_expectation() -> None:
    """Wire-layer test_mcp_server expects 18 tools. Adapter must agree."""
    assert len(mcp_adapter.known_tools()) == 18


def test_every_scope_allowlist_tool_exists_in_adapter() -> None:
    """If an agent is permitted to call a tool by scope.py, the adapter
    must know about that tool. This catches drift where a tool gets
    added to scope without being wired into the adapter."""
    adapter_tools = set(mcp_adapter.known_tools())
    scope_tools: set[str] = set()
    for tools in SPECIALIST_TOOL_ALLOWLIST.values():
        scope_tools.update(tools.keys())
    missing = scope_tools - adapter_tools
    assert not missing, (
        f"scope.py names tools that the adapter doesn't know: {missing}"
    )


def test_call_unknown_tool_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="unknown MCP tool"):
        mcp_adapter.call_tool("not_a_real_tool", {})


def test_call_returns_dict_not_pydantic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter must serialize Pydantic responses to plain dicts so the
    dispatch shim's audit-content invariant ('observation.content.result
    is JSON-serializable') always holds."""
    # Replace the registry entry with a function that returns a Pydantic
    # model, then confirm the adapter unwraps it.
    from pydantic import BaseModel

    class FakeResponse(BaseModel):
        x: int

    def fake_tool() -> FakeResponse:
        return FakeResponse(x=42)

    # Reach into the lazy-init'd registry; it's None until first access.
    mcp_adapter._registry()  # force init
    monkeypatch.setitem(mcp_adapter._REGISTRY, "fake_tool", fake_tool)  # type: ignore[arg-type]

    result = mcp_adapter.call_tool("fake_tool", {})
    assert result == {"x": 42}
    assert isinstance(result, dict)
