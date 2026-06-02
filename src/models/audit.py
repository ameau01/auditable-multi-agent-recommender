"""Pydantic models for the audit trail.

Two top-level record types map 1:1 to the two SQLite tables documented
in `docs/audit-trail.md`:

  - `AuditRecord` -> audit_records table (the reasoning trail)
  - `InternalOpRecord` -> internal_ops table (eval, render — internal)

Each record's `content` field is a typed Pydantic sub-model whose shape
is selected by `type`. The content classes are defined below in two
sections (decision-category content, evidence-category content) for
audit_records, and a third section for internal_ops content.

The store layer accepts records as raw dicts at the wire (so producers
can use simple JSON-able payloads) and validates them against these
models on insert. Read paths return typed instances; queries that don't
care about content can keep it opaque.

See `docs/audit-trail.md` for the column-level schema and the rationale
for the two-table split.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    AgentName,
    OpSubType,
    OpType,
    RecordCategory,
    RecordType,
    Tier,
)


# ============================================================
# Section 1 — Decision-category content models
# ============================================================
# These describe the `content` payload for each decision-side record
# type. All are lenient on extras (extra='allow') because agents may
# emit additional debugging fields during development. The store does
# not enforce content shape per type at write-time except via this
# model's own validation.

_LenientConfig = ConfigDict(extra="allow")


class CycleStartedContent(BaseModel):
    """content for type='cycle_started'. The cycle's root record.
    parent_id MUST be NULL for this record."""
    application_id: str
    scenario_hash: str | None = None
    trigger_type: str = "manual"      # "manual" | "scheduled" | "test"
    notes: str | None = None
    model_config = _LenientConfig


class CycleCompletedContent(BaseModel):
    """content for type='cycle_completed'. The cycle's end tag.
    parent_id MUST point to the cycle_started record's id."""
    final_status: str                   # "completed" | "failed" | "aborted"
    failure_reason: str | None = None
    recommendation_record_id: int | None = None
    model_config = _LenientConfig


class ReviewRequestContent(BaseModel):
    """content for type='review_request'. The ingest trigger."""
    application_id: str
    trigger_source: str | None = None
    notes: str | None = None
    model_config = _LenientConfig


class SupervisorDecisionContent(BaseModel):
    """content for type='supervisor_decision'. Specialist deployment,
    retry, or escalation choice."""
    decision_type: str                  # "invoke_specialist" | "retry" | "escalate" | "aggregate"
    decision_details: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[int] = Field(default_factory=list)
    model_config = _LenientConfig


class ThoughtContent(BaseModel):
    """content for type='thought'. One ReAct loop thought step."""
    thought: str
    evidence_refs: list[int] = Field(default_factory=list)
    model_config = _LenientConfig


class SpecialistFindingContent(BaseModel):
    """content for type='specialist_finding'. A tier specialist's verdict."""
    specialist: AgentName
    finding_type: str
    headline: str | None = None
    primary_tier: Tier | None = None
    confidence: float | None = None
    reasoning_summary: str | None = None
    evidence_refs: list[int] = Field(default_factory=list)
    model_config = _LenientConfig


class EvaluatorRecordContent(BaseModel):
    """content for type='evaluator_record'. Cross-tier evaluator synthesis."""
    cross_tier_interactions: list[dict[str, Any]] = Field(default_factory=list)
    trade_off_scores: dict[str, Any] = Field(default_factory=dict)
    synthesis: dict[str, Any] = Field(default_factory=dict)
    contributing_findings: list[int] = Field(default_factory=list)
    evaluator_confidence: float | None = None
    evidence_refs: list[int] = Field(default_factory=list)
    model_config = _LenientConfig


class RecommendationContent(BaseModel):
    """content for type='recommendation'. The final Composite emitted
    by the cycle. The composite field carries the full artifact; the
    composer reads this field when reconstructing a Composite from the
    cycle's records.

    Stored as dict[str, Any] rather than the Composite class directly
    to keep this file from importing from composite.py (circular
    import risk) and to let the audit store remain agnostic to schema
    changes in Composite."""
    composite: dict[str, Any]
    evidence_refs: list[int] = Field(default_factory=list)
    model_config = _LenientConfig


class GateVerdictContent(BaseModel):
    """content for type='gate_verdict'. Action Harness pass/fail.
    Emitted by the future Action Harness — schema declared now so the
    store accepts it when the harness phase lands."""
    well_formedness_verdict: str | None = None
    evidence_completeness_verdict: str | None = None
    severity_classification: str | None = None
    duplication_check_result: str | None = None
    overall_verdict: str                # "pass" | "flagged" | "rejected"
    evidence_refs: list[int] = Field(default_factory=list)
    model_config = _LenientConfig


class HitlDecisionContent(BaseModel):
    """content for type='hitl_decision'. Human reviewer verdict.
    Emitted in the future when HITL is wired up."""
    decision: str                       # "approve" | "reject" | "defer"
    reviewer_notes: str | None = None
    evidence_refs: list[int] = Field(default_factory=list)
    model_config = _LenientConfig


# ============================================================
# Section 2 — Evidence-category content models
# ============================================================
# Evidence is the leaf of the decision-chain tree. These records don't
# carry evidence_refs themselves — they ARE the evidence other records
# cite via their evidence_refs lists.


class ToolCallContent(BaseModel):
    """content for type='tool_call'. The MCP call's parameters echoed."""
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    scenario_hash: str | None = None
    model_config = _LenientConfig


class ObservationContent(BaseModel):
    """content for type='observation'. The tool result returned to the
    agent. parent_id should point to the matching tool_call record so
    Report 2 (evidence trace) can pair them."""
    tool_name: str
    result: dict[str, Any] = Field(default_factory=dict)
    model_config = _LenientConfig


class CorrelationObservationContent(BaseModel):
    """content for type='correlation_observation'. A specific cross-tier
    correlation cited by an agent."""
    tier_a: str
    metric_a: str
    tier_b: str
    metric_b: str
    coefficient: float | None = None
    lag_minutes: int | None = None
    alignment_score: float | None = None
    description: str | None = None
    model_config = _LenientConfig


class InfrastructureFactContent(BaseModel):
    """content for type='infrastructure_fact'. A specific configuration
    or terraform finding cited as evidence."""
    fact_type: str                      # e.g. "instance_class" | "replica_count" | "tier_topology"
    fact: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None           # e.g. "terraform" | "configuration"
    model_config = _LenientConfig


# ============================================================
# Section 3 — Internal_ops content models
# ============================================================
# These describe content payloads for the second table, internal_ops.
# Decoupled from audit_records — different audience, different lifecycle.


class JudgeCallContent(BaseModel):
    """content for op_type='evaluation', type='judge_call'. The prompt
    sent to the LLM judge — captured so a prompt-tuner can inspect the
    exact input that produced a given score."""
    provider: str                       # "anthropic" | "openai"
    model: str
    prompt: str
    model_config = _LenientConfig


class EvaluatorScoreContent(BaseModel):
    """content for op_type='evaluation', type='evaluator_score'. The
    synthesized ScoreOneResult with all five layer verdicts.

    Stored as dict[str, Any] (the model_dump() of ScoreOneResult) rather
    than the typed class to keep this file decoupled from scoring.py
    schema changes."""
    score_one_result: dict[str, Any]
    judge_call_id: int | None = None    # parent_id within the op chain
    model_config = _LenientConfig


class ReportRenderContent(BaseModel):
    """content for op_type='report_render' or 'evidence_render'."""
    output_path: str
    byte_count: int | None = None
    success: bool = True
    error_message: str | None = None
    model_config = _LenientConfig


# ============================================================
# Section 4 — Base record models (one row each)
# ============================================================


class AuditRecord(BaseModel):
    """One row in the audit_records table. The reasoning-trail event."""
    id: int | None = None               # populated by SQLite after insert
    review_cycle_id: str
    parent_id: int | None = None
    category: RecordCategory
    type: RecordType
    agent: AgentName | None = None
    content: dict[str, Any]             # one of the *Content classes above
    emitted_at: datetime | None = None  # populated by SQLite default

    model_config = ConfigDict(extra="forbid")


class InternalOpRecord(BaseModel):
    """One row in the internal_ops table. A post-hoc operation against
    a completed cycle's recommendation (eval run, report render)."""
    id: int | None = None
    op_id: str                          # e.g. "eval_20260601_142003_a3f8b1c0"
    op_type: OpType
    target_cycle_id: str                # which cycle this op acted on
    target_record_id: int | None = None  # specific record (usually the recommendation)
    parent_id: int | None = None        # self-FK for multi-step ops
    type: OpSubType
    content: dict[str, Any]             # one of the *Content classes above
    emitted_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")
