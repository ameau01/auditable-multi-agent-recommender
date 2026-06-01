"""Scenario and dataset tools (5 tools).

Deliberately kept off every specialist's toolset (per scope.py). These
are for navigation, the System Mapper, the Cross-Tier Evaluator, and the
evaluator harness only.
"""

from __future__ import annotations

from .._common import app_name_to_scenario_id, load_for_app
from ..server import mcp


@mcp.tool()
def list_scenarios() -> dict:
    """Return the catalog of app_names known to the dataset.

    Useful for external clients (Claude Desktop) to enumerate before
    asking for any specific scenario.
    """
    # Import inside the function so test stubs that monkey-patch
    # data_loader take effect.
    from ..data_loader import list_scenario_ids
    sids = list_scenario_ids()
    return {"app_names": [f"app-{sid}" for sid in sids]}


@mcp.tool()
def get_scenario_metadata(app_name: str) -> dict:
    """Return the full metadata document for one application.

    Includes scenario_name, scenario_type, tier_topology, business_context,
    cost_baseline, narrative, scenario_specific_evidence, and the
    before/after evidence if present. The agent reads narrower keys via
    the dedicated tools (get_business_context, get_sla_target, etc.);
    this returns the full document for clients that want everything.
    """
    scenario = load_for_app(app_name)
    return {"app_name": app_name, "metadata": scenario.get("metadata", {})}


@mcp.tool()
def get_terraform(app_name: str) -> dict:
    """Return the Terraform definition for the application's infrastructure.

    Reads main.tf as raw HCL text. The System Mapper parses this to
    derive the dependency graph.
    """
    scenario = load_for_app(app_name)
    return {"app_name": app_name, "terraform": scenario.get("terraform", "")}


@mcp.tool()
def get_correlation_evidence(app_name: str) -> dict:
    """Return the recorded cross-tier correlations for the application.

    Reads correlation_evidence.json. Used by the Cross-Tier Evaluator
    to identify cascade signatures (downstream tier leads upstream).
    Returns the dict as-is; structure varies by scenario but typically
    contains lists of {pair, coefficient, lag_minutes, alignment_score}.
    """
    scenario = load_for_app(app_name)
    return {
        "app_name": app_name,
        "correlation_evidence": scenario.get("correlation_evidence", {}),
    }


@mcp.tool()
def get_handcrafted_recommendation(app_name: str) -> dict:
    """Return the gold-answer recommendation for the application.

    OFF-LIMITS for every specialist (per scope.py). Available only to the
    evaluator harness, which uses it to score agent predictions. Exposing
    this on a specialist's surface would let it reason backward from the
    target, defeating the evaluation.
    """
    scenario = load_for_app(app_name)
    return {
        "app_name": app_name,
        "handcrafted_recommendation": scenario.get("handcrafted_recommendation", {}),
    }
