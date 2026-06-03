"""In-process MCP adapter: agents call tools without the wire protocol.

The MCP server at `src/mcp_server/` registers tools via `@mcp.tool()`.
The decoration is non-invasive — the underlying functions remain
callable as plain Python. This module exposes those functions as a
single `call_tool(name, arguments)` interface so the dispatch shim
doesn't care which tool was asked for.

Why in-process and not stdio:

  - The wire layer is already covered by `tests/integration/test_mcp_server.py`.
  - Subprocess startup for every tool call would add seconds per cycle.
  - Both paths share the same Pydantic response models from
    `src.models.telemetry`, so a working adapter test is a working
    contract test.

Adding a new tool:
  1. Define + decorate it under `src/mcp_server/tools/`.
  2. Import it in `_TOOL_REGISTRY` below.
  3. The dispatch shim picks it up automatically — no other change
     needed in the agent code.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel


# Wire each tool function in here. The keys MUST match the tool names
# that the agents call (which match `scope.py` allow-list entries).
def _build_registry() -> dict[str, Callable[..., Any]]:
    # Lazy imports keep import time low and avoid pulling MCP server
    # state into pure adapter callers.
    from ..mcp_server.tools.context import (
        get_before_after_evidence,
        get_business_context,
        get_monthly_cost,
        get_sla_target,
    )
    from ..mcp_server.tools.scenarios import (
        get_correlation_evidence,
        get_handcrafted_recommendation,
        get_scenario_metadata,
        get_terraform,
        list_scenarios,
    )
    from ..mcp_server.tools.specials import (
        get_per_instance_breakout,
        get_top_cache_keys,
        get_top_queries,
    )
    from ..mcp_server.tools.telemetry import (
        detect_threshold_breaches,
        get_configuration,
        get_metric_distribution,
        get_summary_statistics,
        get_time_pattern,
        get_time_series,
    )

    return {
        # Telemetry (tier-scoped)
        "get_time_series":            get_time_series,
        "get_summary_statistics":     get_summary_statistics,
        "get_time_pattern":           get_time_pattern,
        "detect_threshold_breaches":  detect_threshold_breaches,
        "get_metric_distribution":    get_metric_distribution,
        "get_configuration":          get_configuration,
        # Context (no tier param)
        "get_business_context":       get_business_context,
        "get_sla_target":             get_sla_target,
        "get_monthly_cost":           get_monthly_cost,
        "get_before_after_evidence":  get_before_after_evidence,
        # Specials
        "get_per_instance_breakout":  get_per_instance_breakout,
        "get_top_queries":            get_top_queries,
        "get_top_cache_keys":         get_top_cache_keys,
        # Scenarios / dataset
        "list_scenarios":             list_scenarios,
        "get_scenario_metadata":      get_scenario_metadata,
        "get_terraform":              get_terraform,
        "get_correlation_evidence":   get_correlation_evidence,
        "get_handcrafted_recommendation": get_handcrafted_recommendation,
    }


_REGISTRY: dict[str, Callable[..., Any]] | None = None


def _registry() -> dict[str, Callable[..., Any]]:
    """Lazy-init the tool registry on first call."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke an MCP tool by name and return its response as a dict.

    Args:
        tool_name: must be a key in the registry. Unknown names raise
            `KeyError` — that's a programmer error, not an agent error
            (the ActionHarness should have already rejected the call).
        arguments: keyword arguments passed straight to the tool. The
            tool's own signature validates types.

    Returns:
        The tool's Pydantic response model serialized via
        `model_dump()` so the dispatch shim sees a plain dict
        regardless of which tool was called. This keeps the audit
        trail's `observation.content.result` shape uniform.
    """
    fn = _registry().get(tool_name)
    if fn is None:
        raise KeyError(
            f"unknown MCP tool name: {tool_name!r}. Known: "
            f"{sorted(_registry().keys())}"
        )
    result = fn(**arguments)
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    # Defensive: tools should always return BaseModel; if a future tool
    # returns a plain dict or scalar, wrap it so the caller invariant
    # ("result is JSON-serializable dict") holds.
    return {"value": result}


def known_tools() -> list[str]:
    """Return the registered tool names. Used by tests and the adapter
    consistency check (every tool in scope.py must exist here)."""
    return sorted(_registry().keys())
