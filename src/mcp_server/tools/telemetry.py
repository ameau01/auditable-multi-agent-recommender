"""Per-tier telemetry tools (6 tools, parameterized by tier + metric).

Each tool is a thin wrapper: resolve app_name -> scenario, validate tier,
hand the slice + metric to a `_stats` helper, return its result. All
input validation and ToolError raising lives in `_common.py` so the
catalog stays uniform.

Per `docs/mcp-server.md`, these are the Tier Specialists' vocabulary.
The Action Harness restricts which tier each specialist can pass via
`scope.py` when it lands.
"""

from __future__ import annotations

from mcp.server.fastmcp.exceptions import ToolError

from .. import _stats
from .._common import load_for_app, telemetry_records
from ..server import mcp


@mcp.tool()
def get_time_series(app_name: str, tier: str, metric: str) -> dict:
    """Return the per-window values for one metric on one tier.

    The full timeseries: one entry per 15-minute window, in chronological
    order. Use when you need to inspect every datapoint (rare); for
    summary statistics see `get_summary_statistics`.
    """
    scenario = load_for_app(app_name)
    records = telemetry_records(scenario, tier)
    out = []
    found = False
    for r in records:
        if metric in r:
            found = True
            out.append({"timestamp": r.get("timestamp"), "value": r[metric]})
    if not found:
        raise ToolError(
            f"unknown_metric: metric {metric!r} not found in {tier} telemetry "
            f"for {app_name}. Available metrics include: "
            f"{', '.join(k for k in records[0].keys() if k != 'timestamp')}"
        )
    return {"app_name": app_name, "tier": tier, "metric": metric, "series": out}


@mcp.tool()
def get_summary_statistics(app_name: str, tier: str, metric: str) -> dict:
    """Return p50, p90, p95, and mean for one metric on one tier.

    The default-issue summary the specialists ask first to see if a tier
    is healthy. Returns {"p50": ..., "p90": ..., "p95": ..., "mean": ...}.
    """
    scenario = load_for_app(app_name)
    records = telemetry_records(scenario, tier)
    try:
        stats = _stats.summary_statistics(records, metric)
    except ValueError as e:
        raise ToolError(f"unknown_metric: {e}")
    return {"app_name": app_name, "tier": tier, "metric": metric, **stats}


@mcp.tool()
def get_time_pattern(app_name: str, tier: str, metric: str) -> dict:
    """Return the hour-of-day and weekday breakdown of one metric.

    For each hour 0..23 and weekday 0..6 (Mon..Sun) returns the mean of
    the metric over records that fell in that bucket. None for buckets
    with no records.

    Use this to detect business-hours spikes or weekday/weekend patterns.
    """
    scenario = load_for_app(app_name)
    records = telemetry_records(scenario, tier)
    try:
        pattern = _stats.time_pattern(records, metric)
    except ValueError as e:
        raise ToolError(f"unknown_metric: {e}")
    return {"app_name": app_name, "tier": tier, "metric": metric, **pattern}


@mcp.tool()
def detect_threshold_breaches(
    app_name: str,
    tier: str,
    metric: str,
    threshold: float,
    comparator: str = "gt",
) -> dict:
    """Return the windows where a metric breaches the caller-supplied threshold.

    Args:
      app_name: 'app-NN' identifier.
      tier: 'compute', 'database', 'cache', or 'network'.
      metric: metric field name on the tier's telemetry records.
      threshold: numeric cutoff supplied by the caller (typically derived
          from the SLA target via `get_sla_target`).
      comparator: 'gt' (default; value > threshold) or 'lt'.

    Returns the list of breaching windows plus a count.
    """
    scenario = load_for_app(app_name)
    records = telemetry_records(scenario, tier)
    try:
        breaches = _stats.find_breaches(records, metric, threshold, comparator)
    except ValueError as e:
        # Both 'unknown_metric' and 'invalid comparator' surface here.
        msg = str(e)
        code = "invalid_input" if "comparator" in msg else "unknown_metric"
        raise ToolError(f"{code}: {msg}")
    return {
        "app_name": app_name, "tier": tier, "metric": metric,
        "threshold": threshold, "comparator": comparator,
        "breach_count": len(breaches), "breaches": breaches,
    }


@mcp.tool()
def get_metric_distribution(
    app_name: str,
    tier: str,
    metric: str,
    n_bins: int = 10,
) -> dict:
    """Return a histogram of the metric's values across all records.

    Bins are uniform-width over [min, max]. Use to see whether a metric
    is normally distributed, bimodal, or skewed.
    """
    scenario = load_for_app(app_name)
    records = telemetry_records(scenario, tier)
    try:
        dist = _stats.metric_distribution(records, metric, n_bins=n_bins)
    except ValueError as e:
        msg = str(e)
        code = "invalid_input" if "n_bins" in msg else "unknown_metric"
        raise ToolError(f"{code}: {msg}")
    return {"app_name": app_name, "tier": tier, "metric": metric, **dist}


@mcp.tool()
def get_configuration(app_name: str, tier: str) -> dict:
    """Return the parsed configuration for one tier (instance class, count, etc.).

    Reads from metadata.tier_topology[tier]. Returns the dict as-is; the
    keys vary per tier (compute has scaling policy, database has replicas,
    etc.). Returns None for a tier that isn't present in this scenario.
    """
    scenario = load_for_app(app_name)
    topology = scenario.get("metadata", {}).get("tier_topology", {})
    if tier not in topology:
        raise ToolError(
            f"unknown_tier: tier {tier!r} is not present in tier_topology "
            f"for {app_name}. Known tiers: {sorted(topology.keys())}"
        )
    return {"app_name": app_name, "tier": tier, "configuration": topology[tier]}
