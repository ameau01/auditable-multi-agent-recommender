"""Shared context tools (4 tools, available to every specialist).

All four return slices of metadata.json. Per docs/mcp-server.md these are
the "available to every specialist, independent of tier" surface.
"""

from __future__ import annotations

from mcp.server.fastmcp.exceptions import ToolError

from .._common import load_for_app
from ..server import mcp


@mcp.tool()
def get_business_context(app_name: str) -> dict:
    """Return the application's criticality, description, and tier.

    Reads from metadata.business_context — useful for understanding the
    blast radius of a recommendation (a tier-1 checkout service vs an
    internal analytics tool).
    """
    scenario = load_for_app(app_name)
    bc = scenario.get("metadata", {}).get("business_context")
    if bc is None:
        raise ToolError(f"unknown_metric: business_context missing from {app_name}")
    return {"app_name": app_name, "business_context": bc}


@mcp.tool()
def get_sla_target(app_name: str) -> dict:
    """Return the SLA target (availability + latency).

    Reads from metadata.business_context.sla. The agent uses this to
    derive thresholds for `detect_threshold_breaches`.
    """
    scenario = load_for_app(app_name)
    bc = scenario.get("metadata", {}).get("business_context") or {}
    sla = bc.get("sla")
    if sla is None:
        raise ToolError(f"unknown_metric: sla missing from {app_name} business_context")
    return {"app_name": app_name, "sla": sla}


@mcp.tool()
def get_monthly_cost(app_name: str) -> dict:
    """Return the per-tier and total monthly cost baseline.

    Reads from metadata.cost_baseline.
    """
    scenario = load_for_app(app_name)
    cost = scenario.get("metadata", {}).get("cost_baseline")
    if cost is None:
        raise ToolError(f"unknown_metric: cost_baseline missing from {app_name}")
    return {"app_name": app_name, "cost_baseline": cost}


@mcp.tool()
def get_before_after_evidence(app_name: str) -> dict:
    """Return the before/after observation of a prior config change.

    Reads from metadata.before_after_evidence. Useful when a similar
    change was already trialled and we have a measured outcome to cite.
    Returns {} (empty object) when the scenario has no prior evidence.
    """
    scenario = load_for_app(app_name)
    ev = scenario.get("metadata", {}).get("before_after_evidence", {})
    return {"app_name": app_name, "before_after_evidence": ev}
