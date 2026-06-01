"""Per-specialist tool-and-tier allow-list.

The MCP server itself stays open: every client sees every tool. Scope is
enforced one layer above, in the Action Harness, by consulting this dict.
A specialist may call a tool only if:

  1. The tool name appears in its allow-list, and
  2. If the tool takes a `tier` argument, the value is in the allowed-tiers
     list for this (specialist, tool) pair. `None` means "no tier param,
     no per-tier restriction."

The MCP wire surface is shared by external clients (Claude Desktop, etc.)
who are not bound by this map — they can browse everything. This is the
"impossible at the tool layer" claim's practical landing: it's structural
for the agents, by virtue of the harness allow-list and the per-operation
tool naming on the wire; it's not enforced for external clients because
external clients are humans exploring the dataset.

Tested by tests/unit/mcp_server/test_scope.py, which asserts every name
in this dict resolves to a registered FastMCP tool and every tier in
every constraint is a valid tier name.
"""

from __future__ import annotations


# A tool entry value is either:
#   - None        : tool takes no `tier` arg, no per-tier restriction.
#   - list[str]   : tool takes a `tier` arg; only these tier values are allowed.
SpecialistTools = dict[str, list[str] | None]


# Shared context tools that every specialist may call (no scope restriction).
_SHARED_CONTEXT: SpecialistTools = {
    "get_business_context": None,
    "get_sla_target":       None,
    "get_monthly_cost":     None,
    "get_before_after_evidence": None,
    "get_scenario_metadata":     None,
}


SPECIALIST_TOOL_ALLOWLIST: dict[str, SpecialistTools] = {
    "compute_analyst": {
        "get_time_series":            ["compute"],
        "get_summary_statistics":     ["compute"],
        "get_time_pattern":           ["compute"],
        "detect_threshold_breaches":  ["compute"],
        "get_metric_distribution":    ["compute"],
        "get_configuration":          ["compute"],
        "get_per_instance_breakout":  None,
        **_SHARED_CONTEXT,
    },
    "data_layer_analyst": {
        "get_time_series":            ["database", "cache"],
        "get_summary_statistics":     ["database", "cache"],
        "get_time_pattern":           ["database", "cache"],
        "detect_threshold_breaches":  ["database", "cache"],
        "get_metric_distribution":    ["database", "cache"],
        "get_configuration":          ["database", "cache"],
        "get_top_queries":            None,
        "get_top_cache_keys":         None,
        **_SHARED_CONTEXT,
    },
    "network_analyst": {
        "get_time_series":            ["network"],
        "get_summary_statistics":     ["network"],
        "get_time_pattern":           ["network"],
        "detect_threshold_breaches":  ["network"],
        "get_metric_distribution":    ["network"],
        "get_configuration":          ["network"],
        **_SHARED_CONTEXT,
    },
    "system_mapper": {
        "list_scenarios":         None,
        "get_terraform":          None,
        "get_scenario_metadata":  None,
    },
    "cross_tier_evaluator": {
        "get_correlation_evidence": None,
        "get_scenario_metadata":    None,
        # The evaluator may also re-read per-tier telemetry across all
        # four tiers when synthesizing. It's the only specialist allowed
        # the full cross-tier surface.
        "get_time_series":            ["compute", "database", "cache", "network"],
        "get_summary_statistics":     ["compute", "database", "cache", "network"],
        "get_time_pattern":           ["compute", "database", "cache", "network"],
        "detect_threshold_breaches":  ["compute", "database", "cache", "network"],
        "get_metric_distribution":    ["compute", "database", "cache", "network"],
        "get_configuration":          ["compute", "database", "cache", "network"],
    },
    "evaluator_harness": {
        # The only consumer of the gold answer. Off every specialist's
        # surface so they cannot reason backward from the target.
        "get_handcrafted_recommendation": None,
    },
}
