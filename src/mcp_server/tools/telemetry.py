"""Per-tier telemetry tools (6 tools, parameterized by tier + metric).

Each tool is a thin wrapper: resolve app_name -> scenario, validate tier,
hand the slice + metric to a `_stats` helper, return the typed Pydantic
response. All input validation and ToolError raising lives in
`_common.py` so the catalog stays uniform.

Per `docs/mcp-server.md`, these are the Tier Specialists' vocabulary.
The Action Harness restricts which tier each specialist can pass via
`scope.py` when it lands.

Three responses adopt the envelope+body pattern with a nested body
field (statistics, time_pattern, distribution). DetectThresholdBreachesResponse
keeps its fields flat because they are semantically mixed (echoed
inputs + derived count + list-as-body).
"""

from __future__ import annotations

from mcp.server.fastmcp.exceptions import ToolError

from ...models.telemetry import (
    DetectThresholdBreachesResponse,
    GetConfigurationResponse,
    GetMetricDistributionResponse,
    GetSummaryStatisticsResponse,
    GetTimePatternResponse,
    GetTimeSeriesResponse,
    MetricDistribution,
    MetricStatistics,
    TimePattern,
)
from .. import _stats
from .._common import load_for_app, telemetry_records
from ..server import mcp


@mcp.tool()
def get_time_series(app_name: str, tier: str, metric: str) -> GetTimeSeriesResponse:
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
    # model_validate so mypy sees the dict-to-TimeSeriesPoint coercion;
    # Pydantic handles the per-item validation at runtime.
    return GetTimeSeriesResponse.model_validate({
        "app_name": app_name, "tier": tier, "metric": metric, "series": out,
    })


@mcp.tool()
def get_summary_statistics(
    app_name: str, tier: str, metric: str,
) -> GetSummaryStatisticsResponse:
    """Return p50, p90, p95, and mean for one metric on one tier.

    The default-issue summary the specialists ask first to see if a tier
    is healthy. The four percentiles travel together under `statistics`.
    """
    scenario = load_for_app(app_name)
    records = telemetry_records(scenario, tier)
    try:
        stats = _stats.summary_statistics(records, metric)
    except ValueError as e:
        raise ToolError(f"unknown_metric: {e}")
    return GetSummaryStatisticsResponse(
        app_name=app_name, tier=tier, metric=metric,
        statistics=MetricStatistics.model_validate(stats),
    )


@mcp.tool()
def get_time_pattern(
    app_name: str, tier: str, metric: str,
) -> GetTimePatternResponse:
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
    return GetTimePatternResponse(
        app_name=app_name, tier=tier, metric=metric,
        time_pattern=TimePattern.model_validate(pattern),
    )


@mcp.tool()
def detect_threshold_breaches(
    app_name: str,
    tier: str,
    metric: str,
    threshold: float,
    comparator: str = "gt",
) -> DetectThresholdBreachesResponse:
    """Return the windows where a metric breaches the caller-supplied threshold.

    Args:
      app_name: 'app-NN' identifier.
      tier: 'compute', 'database', 'cache', or 'network'.
      metric: metric field name on the tier's telemetry records.
      threshold: numeric cutoff supplied by the caller (typically derived
          from the SLA target via `get_sla_target`).
      comparator: 'gt' (default; value > threshold) or 'lt'.

    Returns the list of breaching windows plus a count. The response
    keeps a flat shape on purpose: threshold + comparator are echoed
    inputs, breach_count is derived, breaches is the body-as-list.
    """
    scenario = load_for_app(app_name)
    records = telemetry_records(scenario, tier)
    try:
        breaches = _stats.find_breaches(records, metric, threshold, comparator)
    except ValueError as e:
        msg = str(e)
        code = "invalid_input" if "comparator" in msg else "unknown_metric"
        raise ToolError(f"{code}: {msg}")
    # model_validate so mypy sees the dict-to-ThresholdBreach coercion
    # on the breaches list; Pydantic validates each record at runtime.
    return DetectThresholdBreachesResponse.model_validate({
        "app_name": app_name, "tier": tier, "metric": metric,
        "threshold": threshold, "comparator": comparator,
        "breach_count": len(breaches), "breaches": breaches,
    })


@mcp.tool()
def get_metric_distribution(
    app_name: str,
    tier: str,
    metric: str,
    n_bins: int = 10,
) -> GetMetricDistributionResponse:
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
    return GetMetricDistributionResponse(
        app_name=app_name, tier=tier, metric=metric,
        distribution=MetricDistribution.model_validate(dist),
    )


@mcp.tool()
def get_configuration(app_name: str, tier: str) -> GetConfigurationResponse:
    """Return the parsed configuration for one tier (instance class, count, etc.).

    Reads from metadata.tier_topology[tier]. Returns the dict as-is; the
    keys vary per tier (compute has scaling policy, database has replicas,
    etc.). Raises unknown_tier when the tier isn't present.
    """
    scenario = load_for_app(app_name)
    topology = scenario.get("metadata", {}).get("tier_topology", {})
    # Scenarios use both conventions for absent tiers: the tier key may be
    # missing entirely, OR the key may be present with a None value (e.g.
    # `tier_topology: {compute: {...}, database: null, cache: null}`).
    # Both mean "this tier doesn't exist in this scenario." Reject both
    # with the same clear ToolError so the LLM gets a consistent signal.
    tier_config = topology.get(tier)
    if tier_config is None:
        present_tiers = sorted(k for k, v in topology.items() if v is not None)
        raise ToolError(
            f"unknown_tier: tier {tier!r} is not present in tier_topology "
            f"for {app_name}. Present tiers: {present_tiers}"
        )
    return GetConfigurationResponse(
        app_name=app_name, tier=tier, configuration=tier_config,
    )
