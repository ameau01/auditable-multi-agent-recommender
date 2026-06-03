"""Pydantic models for the audit trail.

Three top-level record types map 1:1 to the three SQLite tables documented
in `docs/audit-trail.md`:

  - `AuditRecord`     -> audit_records table (the reasoning trail)
  - `HarnessRecord`   -> harness_trail table (enforcement events)
  - `InternalOpRecord` -> operations table (eval, render — internal)

Each record's `content` field is a typed Pydantic sub-model whose shape
is selected by `type`. The content classes are defined below in four
sections (decision-category content, evidence-category content) for
audit_records, a section for operations content, and a fourth section
for harness_trail content.

The store layer accepts records as raw dicts at the wire (so producers
can use simple JSON-able payloads) and validates them against these
models on insert. Read paths return typed instances; queries that don't
care about content can keep it opaque.

See `docs/audit-trail.md` for the column-level schema and the rationale
for the three-table split.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    AgentName,
    FailureStage,
    HarnessName,
    HarnessRecordType,
    OpSubType,
    OpType,
    RecordCategory,
    RecordType,
    SupervisorDecisionType,
    Tier,
    Verdict,
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

    parent_id MUST point to the cycle_started record's id.

    `failed_at_stage` is the machine-readable counterpart to the prose
    `failure_reason` — set whenever `final_status != 'completed'` so the
    renderer can branch on stage without parsing the reason string. On a
    successful cycle the field is None (there's no failure to attribute).
    """
    final_status: str                   # "completed" | "failed" | "aborted"
    failure_reason: str | None = None
    failed_at_stage: FailureStage | None = None
    recommendation_record_id: int | None = None
    model_config = _LenientConfig


class ReviewRequestContent(BaseModel):
    """content for type='review_request'. The ingest trigger."""
    application_id: str
    trigger_source: str | None = None
    notes: str | None = None
    model_config = _LenientConfig


class SystemMapperOutputContent(BaseModel):
    """content for type='system_mapper_output'. The System Mapper's
    analysis plan: which tiers the application uses, what specialists
    should be invoked, and a brief summary of the parsed terraform.

    Kept distinct from supervisor_decision because the Supervisor and
    the System Mapper play different roles — overloading one record
    type would lose that signal in the trail.
    """
    application_id: str
    tiers_detected: list[Tier] = Field(default_factory=list)
    specialists_to_invoke: list[AgentName] = Field(default_factory=list)
    terraform_resources_summary: str | None = None
    metadata_summary: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    evidence_refs: list[int] = Field(default_factory=list)
    model_config = _LenientConfig


class SupervisorDecisionContent(BaseModel):
    """content for type='supervisor_decision'.

    The Supervisor is the only router in the LangGraph; every transition
    between worker nodes is one of these decisions. The shape is the same
    regardless of which downstream node the decision routes to — the
    `decision_type` discriminates.

    Field contract:

      decision_type   — one of SupervisorDecisionType (see enums.py).
      targets         — for `dispatch_specialists`, the agent names being
                        dispatched (e.g. ["compute_analyst", "data_layer_analyst"]).
                        Empty for non-dispatch decisions.
      terminal_state  — for `decision_type='complete'`, the cycle's
                        terminal label ("completed" | "no_specialists" |
                        "insufficient_data" | etc.). None otherwise.
      reason          — human-readable explanation of *why* this decision.
                        Surfaces in the human-rendered report.
      evidence_refs   — audit_records ids the decision relied on. Required
                        for every decision; the Reasoning Harness's
                        `check_decision_evidence_backed` verifies these
                        resolve to real rows in the same cycle.

    `decision_details` is the catch-all dict for fields the renderer
    doesn't need to special-case (counters, model temperature, etc.).
    """
    decision_type: SupervisorDecisionType
    targets: list[AgentName] = Field(default_factory=list)
    terminal_state: str | None = None
    reason: str
    evidence_refs: list[int] = Field(default_factory=list)
    decision_details: dict[str, Any] = Field(default_factory=dict)
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
# Section 3 — Operations content models
# ============================================================
# These describe content payloads for the `operations` table (renamed
# from `internal_ops` in Phase 11a.2). Decoupled from audit_records —
# different audience, different lifecycle.


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
# Section 4 — Harness_trail content models
# ============================================================
# Harness events split into four categories distinguished by the
# `type` column. Finer-grained sub-checks (which input check failed,
# which gate field failed) live in `content.check_name` rather than
# expanding the type vocabulary — that keeps the trail readable as a
# verdict stream without exploding the enum surface.


class InputValidationContent(BaseModel):
    """content for type='input_validation'. The Input Harness records
    one of these per validation check it runs on the ingest bundle.

    `check_name` distinguishes the specific check (e.g. 'trigger_legitimacy',
    'schema_conformance', 'timestamp_continuity', 'cross_tier_alignment').
    `details` carries the check-specific payload (the offending field, the
    expected vs actual values, etc.).
    """
    check_name: str
    application_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    model_config = _LenientConfig


class ToolCallPolicyCheckContent(BaseModel):
    """content for type='tool_call_policy_check'. The Action Harness
    records one of these per tool call it sees.

    `related_event_id` on the parent HarnessRecord points to the
    audit_records.id of the tool_call event (when permitted) — for
    rejected calls there is no audit_records entry, so related_event_id
    is NULL and this harness_trail row is the only trace.
    """
    agent: AgentName
    tool_name: str
    tier_scope: Tier | None = None      # tier the agent is scoped to (None for cross-tier callers)
    arguments_snapshot: dict[str, Any] = Field(default_factory=dict)
    rejection_reason: str | None = None  # populated when verdict='rejected'
    model_config = _LenientConfig


class GateVerdictContent(BaseModel):
    """content for type='gate_verdict'. Action Harness final-recommendation
    gate. One row per recommendation that reaches the gate.

    Each sub-check carries its own verdict so a reviewer can see which
    aspect failed. `overall_verdict` matches the parent HarnessRecord's
    `verdict` column.
    """
    target_record_id: int               # audit_records.id of the recommendation
    well_formedness_verdict: Verdict | None = None
    evidence_completeness_verdict: Verdict | None = None
    severity_classification: str | None = None  # "low" | "medium" | "high"
    duplication_check_result: Verdict | None = None
    overall_verdict: Verdict
    rejection_reason: str | None = None
    model_config = _LenientConfig


class ReasoningCheckContent(BaseModel):
    """content for type='reasoning_check'. The Reasoning Harness records
    one of these per structured-output pre-emit check (evidence_refs
    minimum count, finding_type in the three-valued set, confidence
    breakdown shape, and so on).

    `check_name` distinguishes the specific check. `target_event_type`
    names the audit_records event this check applies to (e.g.
    'specialist_finding', 'evaluator_record').
    """
    check_name: str
    target_event_type: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    model_config = _LenientConfig


class OrchestrationCheckContent(BaseModel):
    """content for type='orchestration_check'. The Orchestration Harness
    records one of these per cycle-level transition check. In Phase
    11a.4 there is exactly one such check, `cycle_completion_legitimate`,
    which fires immediately before `cycle_completed` is written and
    validates that the terminal_state is consistent with what actually
    happened in the cycle. Phase 11b+ will add additional checks at
    other transition points (e.g. validate_specialists_completed,
    should_proceed_to_evaluator).

    `check_name` distinguishes the specific check. `details` carries
    the input snapshot the check evaluated (final_status, failed_at_stage,
    specialists_invoked) so the verdict is reproducible from the row
    alone without joining audit_records.
    """
    check_name: str
    target_event_type: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    model_config = _LenientConfig


# ============================================================
# Section 5 — Base record models (one row each)
# ============================================================


class AuditRecord(BaseModel):
    """One row in the audit_records table. The reasoning-trail event.

    Column names align with the SQL schema: `cycle_id` (was
    `review_cycle_id` until Phase 11a.2), `timestamp` (was `emitted_at`).
    """
    id: int | None = None               # populated by SQLite after insert
    cycle_id: str
    parent_id: int | None = None
    category: RecordCategory
    type: RecordType
    agent: AgentName | None = None
    content: dict[str, Any]             # one of the *Content classes above
    timestamp: datetime | None = None   # populated by SQLite default

    model_config = ConfigDict(extra="forbid")


class HarnessRecord(BaseModel):
    """One row in the harness_trail table. An enforcement event.

    Substance vs enforcement:
      - When the Action Harness *allows* a tool call, the tool_call +
        observation rows go into audit_records (the substance) and a
        tool_call_policy_check row goes into harness_trail with
        `related_event_id` pointing to the audit tool_call row.
      - When the Action Harness *rejects* a tool call, there is no
        audit_records entry. The rejection lives only here, with
        `related_event_id` NULL.

    parent_id is the self-FK for chaining related harness checks (e.g.
    a gate_verdict whose sub-checks are individual rows).
    """
    id: int | None = None
    cycle_id: str
    parent_id: int | None = None
    related_event_id: int | None = None  # FK reference into audit_records.id
    harness: HarnessName
    type: HarnessRecordType
    verdict: Verdict
    content: dict[str, Any]              # one of the harness *Content classes above
    timestamp: datetime | None = None

    model_config = ConfigDict(extra="forbid")


class InternalOpRecord(BaseModel):
    """One row in the `operations` table (renamed from `internal_ops`
    in Phase 11a.2). A post-hoc operation against a completed cycle's
    recommendation (eval run, report render).

    The class name stays `InternalOpRecord` for now — the *table* was
    renamed for user-visible clarity, but the class name change has a
    larger blast radius (imports across modules) and isn't user-visible.
    """
    id: int | None = None
    op_id: str                          # e.g. "eval_20260601_142003_a3f8b1c0"
    op_type: OpType
    target_cycle_id: str                # which cycle this op acted on
    target_record_id: int | None = None  # specific record (usually the recommendation)
    parent_id: int | None = None        # self-FK for multi-step ops
    type: OpSubType
    content: dict[str, Any]             # one of the *Content classes above
    timestamp: datetime | None = None

    model_config = ConfigDict(extra="forbid")
