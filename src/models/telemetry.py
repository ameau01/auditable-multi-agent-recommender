"""Pydantic output schemas for the MCP server's tools.

Every tool in `src/mcp_server/tools/` annotates its return as one of the
response classes defined here. FastMCP:

  - publishes the JSON Schema as the tool's `outputSchema`
  - serializes the response with shape validation server-side
  - populates `structuredContent` for newer MCP clients

The file is organized in four sections:

  1. Base envelopes — shared by every per-app response (AppResponse,
     TierResponse, TierMetricResponse). Pure Python inheritance; the
     wire shape is flat (Pydantic flattens inherited fields).
  2. Record types — the element shape inside any `list[...]` field
     (TimeSeriesPoint, ThresholdBreach, DistributionBin, TopQueryRecord,
     TopCacheKeyRecord, InstanceRecord, CorrelationRecord, CostByTier).
  3. Body payloads — the named-body half of envelope+body responses
     (MetricStatistics, TimePattern, MetricDistribution, BusinessContext,
     SlaTarget, CostBaseline, BeforeAfterEvidence). Names carry no
     "Payload" suffix; the meaningful distinction is Response vs body.
  4. Response envelopes — one per MCP tool (17 of them; the eighteenth,
     `get_handcrafted_recommendation`, reuses `src.models.composite.Composite`).

Three responses adopt the envelope+body pattern explicitly with a
nested body field (statistics, time_pattern, distribution); three more
already had it (business_context, cost_baseline, before_after_evidence).
The threshold-breach response keeps a flat shape on purpose because
its fields are semantically mixed (echoed inputs + derived count +
list-as-body); forcing a wrapper would bundle things that don't
belong together.

For the deferral reasoning on TimestampedValue, threshold-breach
normalization, and generic TierBuckets, see the "Future extractions,
deferred" subsection in `docs/mcp-server.md`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_StrictConfig = ConfigDict(extra="forbid")
_PassThroughConfig = ConfigDict(extra="allow")


# ============================================================
# Section 1 — Base envelopes
# ============================================================
class AppResponse(BaseModel):
    """Every per-app tool response carries app_name as its first field.
    Inheriting from this base keeps the envelope shape uniform across
    the 17 typed tools.
    """
    app_name: str
    model_config = _StrictConfig


class TierResponse(AppResponse):
    """Adds the tier scope for tools that act on one tier."""
    tier: str


class TierMetricResponse(TierResponse):
    """Adds metric for tools that act on one tier + one metric."""
    metric: str


# ============================================================
# Section 2 — Record types
# ============================================================
class TimeSeriesPoint(BaseModel):
    """One (timestamp, value) point of a metric series. `value` can be
    None for windows with no observation."""
    timestamp: str
    value: float | None
    model_config = _StrictConfig


class ThresholdBreach(BaseModel):
    """One breach event. `value` is never None by definition — a breach
    is a record where the metric crossed the threshold."""
    timestamp: str
    value: float
    model_config = _StrictConfig


class DistributionBin(BaseModel):
    lo: float
    hi: float
    count: int
    model_config = _StrictConfig


class TopQueryRecord(BaseModel):
    """One slow-query record from metadata.scenario_specific_evidence.top_queries.
    extra='allow' tolerates scenario-specific additions."""
    query_text: str
    count: int
    p95_latency_ms: float
    model_config = _PassThroughConfig


class TopCacheKeyRecord(BaseModel):
    """One cache-key pattern record. Shape verified from scenario 07
    in the published dataset."""
    key_pattern: str
    hit_count: int
    miss_count: int
    model_config = _PassThroughConfig


class InstanceRecord(BaseModel):
    """One per-instance imbalance record. Shape verified from scenario 05
    in the published dataset; `cpu_band` is a human-readable string like
    '78-88% sustained (hot)'. extra='allow' lets the data-gen pipeline
    add numeric fields later (e.g. cpu_p95, request_share)."""
    instance_id: str
    cpu_band: str
    model_config = _PassThroughConfig


class CorrelationRecord(BaseModel):
    """One cross-tier correlation record from correlation_evidence.json."""
    tier_a: str
    metric_a: str
    tier_b: str
    metric_b: str
    coefficient: float
    lag_minutes: int
    alignment_score: float
    description: str | None = None
    model_config = _PassThroughConfig


class CostByTier(BaseModel):
    """Numeric cost breakdown keyed by the four canonical tiers. A more
    generic TierBuckets[T] was considered and deferred; see the note in
    docs/mcp-server.md."""
    compute: float | None = None
    database: float | None = None
    cache: float | None = None
    network: float | None = None
    model_config = _StrictConfig


# ============================================================
# Section 3 — Body payloads
# ============================================================
class MetricStatistics(BaseModel):
    """The four canonical percentiles plus mean. Bundled because a stats
    panel is a single cohesive thing."""
    mean: float
    p50: float
    p90: float
    p95: float
    model_config = _StrictConfig


class TimePattern(BaseModel):
    """Hour-of-day and weekday breakdown for one metric, plus the
    sample count that fed them."""
    by_hour_of_day: dict[int, float | None]   # keys 0..23
    by_weekday: dict[int, float | None]       # keys 0..6 (Mon..Sun)
    n_records: int
    model_config = _StrictConfig


class MetricDistribution(BaseModel):
    """Histogram of one metric across all records."""
    min: float
    max: float
    n_bins: int
    bins: list[DistributionBin]
    model_config = _StrictConfig


class BusinessContext(BaseModel):
    """Mirrors metadata.business_context. Tolerates scenario-specific
    extras beyond the core five fields."""
    description: str | None = None
    sla_target_description: str | None = None
    sla_target_p95_ms: float | None = None
    sla_target_availability_pct: float | None = None
    criticality: str | None = None
    model_config = _PassThroughConfig


class SlaTarget(BaseModel):
    """SLA-only extract of business_context. Field names strip the
    sla_target_ prefix that lives on the source. All-None payload is a
    valid return when the scenario has no SLA data (never observed in
    the current 18 scenarios, but the model tolerates it for safety)."""
    description: str | None = None
    p95_ms: float | None = None
    availability_pct: float | None = None
    model_config = _StrictConfig


class CostBaseline(BaseModel):
    """Mirrors metadata.cost_baseline."""
    monthly_cost_total_usd: float
    by_tier: CostByTier
    model_config = _StrictConfig


class BeforeAfterEvidence(BaseModel):
    """Mirrors metadata.before_after_evidence. All fields optional
    because some scenarios have no prior config-change evidence.

    config_before / config_after accept BOTH dict and str shapes — scenario
    metadata authored before the typed-models refactor used short string
    summaries (e.g. 't3.large × 8 fixed, no scaling'); newer scenarios use
    structured dicts. Both are valid prior-state evidence; rejecting the
    string form would cause `get_before_after_evidence` to fail on every
    pre-refactor scenario, which is most of them."""
    config_before: dict[str, Any] | str | None = None
    config_after: dict[str, Any] | str | None = None
    observed_outcome_summary: str | None = None
    source_attribution: str | None = None
    model_config = _PassThroughConfig


# ============================================================
# Section 4 — Response envelopes
# ============================================================

# --- Scenario / dataset tools ---
class ListScenariosResponse(BaseModel):
    """Catalog-level; does not inherit AppResponse because there is no
    single app to scope to."""
    app_names: list[str] = Field(
        description="App identifiers in 'app-NN' format, two-digit zero-padded."
    )
    model_config = _StrictConfig


class GetScenarioMetadataResponse(AppResponse):
    """Full metadata document. Kept loose because consumers that want a
    typed slice should call the narrower tools (get_business_context,
    get_sla_target, ...)."""
    metadata: dict[str, Any]


class GetTerraformResponse(AppResponse):
    terraform: str = Field(description="Raw HCL text of the application's main.tf.")


class GetCorrelationEvidenceResponse(AppResponse):
    correlation_evidence: list[CorrelationRecord]


# --- Context tools ---
class BusinessContextResponse(AppResponse):
    business_context: BusinessContext


class SlaTargetResponse(AppResponse):
    """Returns the SLA targets extracted from business_context. The
    field rename from `sla` to `sla_target` closes Issue-SLA: the old
    body read business_context.sla (which was always None) instead of
    the flat sla_target_* fields where the data actually lives."""
    sla_target: SlaTarget


class MonthlyCostResponse(AppResponse):
    cost_baseline: CostBaseline


class BeforeAfterEvidenceResponse(AppResponse):
    before_after_evidence: BeforeAfterEvidence


# --- Specials ---
class TopQueriesResponse(AppResponse):
    top_queries: list[TopQueryRecord]


class TopCacheKeysResponse(AppResponse):
    top_cache_keys: list[TopCacheKeyRecord]


class PerInstanceBreakoutResponse(AppResponse):
    """Per-instance breakdown evidence. Closes Issue-PIB: the previous
    tool read metadata.scenario_specific_evidence.per_instance_imbalance,
    which does not exist in the data; the real key is per_instance_breakdown.
    The response field name now matches the source-of-truth key."""
    per_instance_breakdown: list[InstanceRecord]


# --- Per-tier telemetry ---
class GetConfigurationResponse(TierResponse):
    """tier-specific shape (compute has scaling_policy, database has
    replicas, etc.); kept as dict[str, Any] rather than modeling every
    tier's fields."""
    configuration: dict[str, Any]


class GetTimeSeriesResponse(TierMetricResponse):
    series: list[TimeSeriesPoint]


class GetSummaryStatisticsResponse(TierMetricResponse):
    """Envelope + named body: the four percentiles live under
    `statistics`, mirroring the business_context / cost_baseline /
    before_after_evidence convention."""
    statistics: MetricStatistics


class GetTimePatternResponse(TierMetricResponse):
    """Envelope + named body."""
    time_pattern: TimePattern


class DetectThresholdBreachesResponse(TierMetricResponse):
    """Stays flat by design. The four fields are semantically mixed:
    `threshold` and `comparator` are echoed inputs (like app_name/tier),
    `breach_count` is a derived count, `breaches` is the actual body-as-list.
    Forcing a wrapper would bundle things that don't belong together.
    See the deferral note in docs/mcp-server.md."""
    threshold: float                    # input echo
    comparator: str                     # input echo ('gt' or 'lt')
    breach_count: int                   # derived
    breaches: list[ThresholdBreach]


class GetMetricDistributionResponse(TierMetricResponse):
    """Envelope + named body."""
    distribution: MetricDistribution
