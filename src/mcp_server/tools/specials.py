"""Per-tier specials (3 tools, scoped to specific specialists by scope.py).

Each tool reads from metadata.scenario_specific_evidence. The keys it
looks for are scenario-dependent: only some scenarios carry top_queries,
only some carry top_cache_keys, etc. A missing key returns an empty
result rather than raising — the agent should treat the absence as
"this scenario does not have that specific evidence."
"""

from __future__ import annotations

from .._common import load_for_app
from ..server import mcp


@mcp.tool()
def get_per_instance_breakout(app_name: str) -> dict:
    """Return per-instance imbalance evidence for a Compute scenario.

    Reads from metadata.scenario_specific_evidence.per_instance_imbalance.
    Returns {} when the scenario has no per-instance evidence.
    """
    scenario = load_for_app(app_name)
    ev = scenario.get("metadata", {}).get("scenario_specific_evidence", {})
    return {
        "app_name": app_name,
        "per_instance_imbalance": ev.get("per_instance_imbalance", {}),
    }


@mcp.tool()
def get_top_queries(app_name: str) -> dict:
    """Return the top-N slowest SQL queries with counts and p95 latency.

    Reads from metadata.scenario_specific_evidence.top_queries. Returns
    an empty list when the scenario doesn't carry query evidence.
    """
    scenario = load_for_app(app_name)
    ev = scenario.get("metadata", {}).get("scenario_specific_evidence", {})
    return {"app_name": app_name, "top_queries": ev.get("top_queries", [])}


@mcp.tool()
def get_top_cache_keys(app_name: str) -> dict:
    """Return the top-N hottest cache-key patterns with hit/miss counts.

    Reads from metadata.scenario_specific_evidence.top_cache_keys.
    Returns an empty list when the scenario doesn't carry cache evidence.
    """
    scenario = load_for_app(app_name)
    ev = scenario.get("metadata", {}).get("scenario_specific_evidence", {})
    return {"app_name": app_name, "top_cache_keys": ev.get("top_cache_keys", [])}
