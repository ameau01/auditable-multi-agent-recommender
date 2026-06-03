"""Cross-cutting Literal types shared by Pydantic models and store layers.

This is the single home for type vocabulary used by multiple subsystems:

  - `Tier` — the four canonical infrastructure tiers, used by MCP responses,
    scope.py allow-lists, evaluator validators, audit AgentName context.
  - `FindingType`, `ActionCategory` — domain enums used by Composite, the
    evaluator, and audit event content.
  - `AgentName` — the 12 agents and harnesses that can emit audit records.
  - `RecordCategory`, `RecordType` — audit-trail event taxonomy.
  - `HarnessName`, `Verdict`, `HarnessRecordType` — harness_trail vocabulary
    (the second table, recording enforcement events).

The frozenset-based runtime universes (FINDING_TYPES, PRIMARY_TIERS, etc.)
that the evaluator's rules-validator uses live in `src/evaluator/enums.py`
and are derived from these Literals — this file is the single source of
truth, the evaluator's frozensets are runtime projections.

When adding a new value:
  1. Add it to the appropriate Literal here.
  2. The evaluator's frozenset auto-includes it via re-derivation.
  3. Update `docs/eval-set.md` enum reference + any composite gold
     answers that should use the new value.
"""

from __future__ import annotations

from typing import Literal, get_args


# ============================================================
# Tier — the four canonical infrastructure tiers
# ============================================================
# Used by: MCP responses (tier-scoped tools), src/mcp_server/scope.py
# ALLOWED_TIERS, src/evaluator/enums.py PRIMARY_TIERS, audit event
# content for tier-specific findings.
Tier = Literal["compute", "database", "cache", "network"]


# ============================================================
# Tier with deferred sentinel — for diagnostic_deferral scenarios
# ============================================================
# A handful of scenarios (15, 17 today) resolve to "deferred" — there is
# no actionable tier because the right answer is to defer pending more
# evidence. The evaluator's PRIMARY_TIERS uses this extended set.
TierOrDeferred = Literal["compute", "database", "cache", "network", "deferred"]


# ============================================================
# FindingType — what kind of conclusion a recommendation reaches
# ============================================================
# `insufficient_data` is forward-compatible — declared but not yet used
# in any gold answer. The evaluator's NO_ACTION_FINDINGS sentinel set
# (in src/evaluator/enums.py) groups the three non-action variants.
FindingType = Literal[
    "issue_found",
    "no_issue_found",
    "diagnostic_deferral",
    "insufficient_data",
]


# ============================================================
# ActionCategory — the concrete change being recommended
# ============================================================
# `pool_sizing` and `replica_adjustment` are reserved; declared for
# completeness, not currently produced by any gold answer.
ActionCategory = Literal[
    "rightsizing",
    "scaling_policy_change",
    "query_cache_optimization",
    "cache_capacity_adjustment",
    "load_balancer_reconfiguration",
    "network_topology_change",
    "sla_review",
    "pool_sizing",
    "replica_adjustment",
]


# ============================================================
# AgentName — every entity that can emit an audit record
# ============================================================
# Twelve values covering: the orchestration (supervisor, system
# mapper, three tier specialists, cross-tier evaluator), the eval-set
# scorer (evaluator_harness), four harness emitters (input/action/
# reasoning/audit), and the human reviewer.
#
# The future-emitter values (input_harness, action_harness, reasoning_
# harness, hitl_decision via human_reviewer) are declared now even
# though their emit code lands later — keeps the Literal complete so
# the audit store accepts records from them when the harness phase ships.
AgentName = Literal[
    "supervisor",
    "system_mapper",
    "compute_analyst",
    "data_layer_analyst",
    "network_analyst",
    "cross_tier_evaluator",
    "evaluator_harness",
    "input_harness",
    "action_harness",
    "reasoning_harness",
    "audit_harness",
    "human_reviewer",
]


# ============================================================
# RecordCategory — the two-category split serving the two reports
# ============================================================
# - "decision" : agent or harness chose, concluded, or judged something.
#                Report 1 (key-decision traceability) walks these.
# - "evidence" : raw observation or data point. Report 2 (evidence
#                traceability) starts here and walks forward via
#                content.evidence_refs.
RecordCategory = Literal["decision", "evidence"]


# ============================================================
# RecordType — the audit-trail event taxonomy (audit_records.type values)
# ============================================================
# Grouped by category. Each value is the `type` column for a row in the
# audit_records table; the per-type content shape is defined by a matching
# Pydantic class in src/models/audit.py.
RecordType = Literal[
    # ---- decision-category types ----
    "cycle_started",              # begin tag (parent_id NULL)
    "cycle_completed",            # end tag (parent_id = cycle_started.id)
    "review_request",             # ingest trigger
    "system_mapper_output",       # System Mapper's analysis plan + tier graph
    "supervisor_decision",        # specialist deployment, retry, escalate
    "thought",                    # one ReAct loop thought step
    "specialist_finding",         # a tier specialist's verdict
    "evaluator_record",           # cross-tier evaluator synthesis
    "recommendation",             # final Composite emitted by the cycle
    "hitl_decision",              # human approve/reject/defer (future)
    # ---- evidence-category types ----
    "tool_call",                  # MCP tool invocation
    "observation",                # tool result (the data the agent saw)
    "correlation_observation",    # specific cross-tier correlation cited
    "infrastructure_fact",        # specific configuration or terraform fact
]
# Note: gate_verdict moved to HarnessRecordType (harness_trail table) —
# it is an enforcement event, not a reasoning event. See docs/audit-trail.md
# "substance vs enforcement" split.


# ============================================================
# HarnessName — which harness emitted a harness_trail event
# ============================================================
# The Input Harness validates the ingest bundle, the Action Harness
# scopes tool calls and gates the final recommendation, and the
# Reasoning Harness enforces structured output and evidence-binding.
# audit_harness is intentionally absent: the audit trail is not itself
# a harness writer here, it is the substrate.
HarnessName = Literal["input", "action", "reasoning", "orchestration"]


# ============================================================
# Verdict — the four outcomes a harness check can record
# ============================================================
# - "passed"   : check succeeded; no concern
# - "rejected" : check failed and the underlying action was blocked
# - "flagged"  : check raised a concern but did not block (HITL signal)
# - "info"     : informational record, no pass/fail semantic (e.g. a
#                trigger ingest summary record)
Verdict = Literal["passed", "rejected", "flagged", "info"]


# ============================================================
# HarnessRecordType — harness_trail.type vocabulary
# ============================================================
# One row per enforcement event. The four broad categories below are
# distinguished by the writing harness; finer distinctions (which check
# inside an input validation, which gate field failed) live in the
# `content` payload's `check_name` field rather than expanding this
# Literal.
HarnessRecordType = Literal[
    "input_validation",           # Input Harness — schema, completeness, trigger
    "tool_call_policy_check",     # Action Harness — per-call scope verdict
    "gate_verdict",               # Action Harness — final recommendation gate
    "reasoning_check",            # Reasoning Harness — structured-output pre-emit
    "orchestration_check",        # Orchestration Harness — cycle-level transition
]


# ============================================================
# OrchestrationCheckName — what an Orchestration Harness row asserts
# ============================================================
# One value for now; reserved Literals for the 11b+ orchestration checks
# (validate_specialists_completed, should_proceed_to_evaluator) are
# deliberately omitted here and will land alongside the specialists.
OrchestrationCheckName = Literal["cycle_completion_legitimate"]


# ============================================================
# FailureStage — machine-readable label for where a cycle terminated
# ============================================================
# Stamped on `cycle_completed.content.failed_at_stage` whenever the
# cycle's `final_status` is not "completed". Lets the renderer branch
# on stage without parsing the prose `failure_reason` string.
#
# Stages map 1:1 to the orchestration nodes:
#   input_harness — Input Harness rejected the trigger/scenario.
#   system_mapper — System Mapper failed (parse error, MCP failure).
#   supervisor    — Supervisor itself errored or rejected its own decision.
#   specialist    — A tier specialist (compute/data/network) raised.
#   evaluator     — Cross-Tier Evaluator failed to synthesize.
#   gate          — Action Harness recommendation gate rejected.
#
# On a successful cycle (final_status="completed"), failed_at_stage is
# absent — there's no failure to attribute.
FailureStage = Literal[
    "input_harness",
    "system_mapper",
    "supervisor",
    "specialist",
    "evaluator",
    "gate",
    "orchestration",
]


# ============================================================
# SupervisorDecisionType — the supervisor's routing decision space
# ============================================================
# Stamped on `supervisor_decision.content.decision_type`. The Supervisor
# is the only router in the LangGraph; every transition between worker
# nodes is its decision. This Literal enumerates the choices it can make.
#
#   dispatch_system_mapper — ask System Mapper to produce tier_topology.
#                            Typically the first decision in any cycle.
#   dispatch_specialists   — fan out to one or more tier specialists.
#                            Decision payload's `targets` lists which.
#   synthesize             — hand the collected findings to the
#                            Cross-Tier Evaluator. (Reserved for 11d.)
#   gate                   — send the recommendation to the Action
#                            Harness gate. (Reserved for 11e.)
#   complete               — terminate the cycle. The `terminal_state`
#                            field on the payload (`completed`,
#                            `no_specialists`, `insufficient_data`, etc.)
#                            says why.
#
# Every decision must carry `evidence_refs` — the audit_records ids the
# decision relied on — so the Reasoning Harness can verify each routing
# decision is evidence-backed.
SupervisorDecisionType = Literal[
    "dispatch_system_mapper",
    "dispatch_specialists",
    "synthesize",
    "gate",
    "complete",
]


# ============================================================
# OpType — operations.op_type values (the post-hoc operations)
# ============================================================
# Distinct from RecordType because operations is a separate table
# tracking operations performed AFTER a cycle ends (eval, render).
OpType = Literal[
    "evaluation",
    "report_render",
    "evidence_render",
]


# ============================================================
# OpSubType — the per-event types within an operations record chain
# ============================================================
OpSubType = Literal[
    # evaluation chain
    "judge_call",                 # the prompt sent to the LLM judge
    "evaluator_score",            # synthesized ScoreOneResult
    # render chain
    "render_started",
    "render_completed",
]


# ============================================================
# Runtime universes derived from the Literals
# ============================================================
# The Scorer's rules.py validator consumes frozenset universes.
# Deriving them here from the Literals via typing.get_args means the
# universe always matches the Literal — no manual sync needed.
TIERS: frozenset[str] = frozenset(get_args(Tier))
TIERS_OR_DEFERRED: frozenset[str] = frozenset(get_args(TierOrDeferred))
FINDING_TYPE_VALUES: frozenset[str] = frozenset(get_args(FindingType))
ACTION_CATEGORY_VALUES: frozenset[str] = frozenset(get_args(ActionCategory))
AGENT_NAMES: frozenset[str] = frozenset(get_args(AgentName))
RECORD_CATEGORIES: frozenset[str] = frozenset(get_args(RecordCategory))
RECORD_TYPES: frozenset[str] = frozenset(get_args(RecordType))
OP_TYPES: frozenset[str] = frozenset(get_args(OpType))
HARNESS_NAMES: frozenset[str] = frozenset(get_args(HarnessName))
VERDICTS: frozenset[str] = frozenset(get_args(Verdict))
HARNESS_RECORD_TYPES: frozenset[str] = frozenset(get_args(HarnessRecordType))
ORCHESTRATION_CHECK_NAMES: frozenset[str] = frozenset(get_args(OrchestrationCheckName))
FAILURE_STAGES: frozenset[str] = frozenset(get_args(FailureStage))
SUPERVISOR_DECISION_TYPES: frozenset[str] = frozenset(get_args(SupervisorDecisionType))
