"""System Mapper node.

The System Mapper is the first agent the Supervisor calls. It parses
the application's terraform + metadata, determines which infrastructure
tiers are present, and emits an `AnalysisPlan` naming the specialists
the Supervisor should invoke.

Code-only in Phase 11a. The System Mapper does not call the LLM here
— terraform parsing and tier detection are deterministic. A future
phase may augment the plan with LLM-guided analysis (e.g. anticipating
multi-tier interactions), at which point this node grows an LLM hook.

Tier-to-specialist mapping (matches `src/mcp_server/scope.py`):
  - compute         → compute_analyst
  - database, cache → data_layer_analyst (single specialist for both)
  - network         → network_analyst

The mapping is one-way: a tier may have zero or one specialist, and a
specialist may cover multiple tiers (data_layer_analyst covers both
database and cache). System Mapper de-duplicates specialists so the
fan-out list has unique values.
"""

from __future__ import annotations

from typing import Any

from ..audit.store import AuditStore
from ..harnesses.action import ActionHarness
from ..harnesses.reasoning import ReasoningHarness
from ..models.audit import AuditRecord
from ..models.enums import AgentName, Tier
from .analysis_plan import AnalysisPlan
from .dispatch import dispatch_tool
from .state import CycleState


# Tier → specialist owner. None means "no specialist exists for this
# tier in the current build" (i.e. tier is detected but the agent layer
# can't dispatch a specialist for it — that's a 11b+ wiring concern).
TIER_TO_SPECIALIST: dict[Tier, AgentName] = {
    "compute":  "compute_analyst",
    "database": "data_layer_analyst",
    "cache":    "data_layer_analyst",
    "network":  "network_analyst",
}


class SystemMapperError(RuntimeError):
    """Raised when the System Mapper cannot produce an analysis plan
    (e.g. terraform fetch was harness-rejected, metadata is missing
    tier_topology, etc.). The orchestrator catches this and routes to
    cycle_complete with terminal_state='failed'."""


class SystemMapperNode:
    """Stateless functor. Construct once per cycle; call once per cycle."""

    def __init__(
        self,
        store: AuditStore,
        action_harness: ActionHarness,
        reasoning_harness: ReasoningHarness,
    ) -> None:
        self._store = store
        self._action_harness = action_harness
        self._reasoning = reasoning_harness

    # ----------------------------------------------------------------
    # Public entry point (called by the orchestrator graph)
    # ----------------------------------------------------------------
    def run(self, state: CycleState) -> dict[str, Any]:
        """Build an AnalysisPlan for the cycle's application.

        Returns a partial-state dict that LangGraph merges into the
        full CycleState. The merge sets state.analysis_plan,
        state.has_system_map, and state.last_system_mapper_output_id —
        the last so the Supervisor can cite this row as evidence when
        it routes the next decision.

        The System Mapper's tier-detection conclusion is itself a
        decision: it cites the observation rows from its MCP fetches
        as evidence_refs, and routes that citation through the
        Reasoning Harness so the "every decision is evidence-backed"
        invariant covers System Mapper output, not just Supervisor.
        """
        plan, evidence_refs = self._build_plan(state)
        # Hard gate: route the conclusion through the Reasoning Harness.
        # A rejection (missing/dangling/foreign refs) is a System Mapper
        # failure — the orchestrator wrapper catches SystemMapperError and
        # routes to cycle_complete with failed_at_stage='system_mapper'.
        check = self._reasoning.check_decision_evidence_backed(
            cycle_id=state.cycle_id,
            decision_payload={"evidence_refs": evidence_refs},
            related_event_id=None,
            record_type="system_mapper_output",
        )
        if not check.passed:
            raise SystemMapperError(
                f"Reasoning Harness rejected the system_mapper_output "
                f"({check.failure_reason})."
            )
        row_id = self._record_output(state, plan, evidence_refs)
        return {
            "analysis_plan": plan,
            "has_system_map": True,
            "last_system_mapper_output_id": row_id,
        }

    # ----------------------------------------------------------------
    # Internal: assemble the plan
    # ----------------------------------------------------------------
    def _build_plan(
        self, state: CycleState,
    ) -> tuple[AnalysisPlan, list[int]]:
        """Return the plan plus the audit_records ids of the observations
        the plan was derived from. Those ids become the plan row's
        `evidence_refs`, so a reader of `system_mapper_output` can walk
        back to the exact MCP observations that produced the tier
        detection and terraform summary."""
        # Pull metadata (gives us tier_topology — the deterministic
        # source-of-truth for which tiers are present). The MCP response
        # `GetScenarioMetadataResponse` wraps the actual document in a
        # `metadata` envelope alongside `app_name`; unwrap to the inner
        # dict before reading tier_topology.
        envelope, metadata_obs_id = self._fetch("get_scenario_metadata", state)
        metadata = envelope.get("metadata") or {}
        # Pull terraform for the resources_summary — useful in the audit
        # trail even though we don't structurally need it. The terraform
        # response carries `terraform` at the top level alongside
        # `app_name`; no envelope unwrap needed.
        terraform_payload, terraform_obs_id = self._fetch("get_terraform", state)

        tier_topology = metadata.get("tier_topology") or {}
        tiers_detected = self._extract_present_tiers(tier_topology)
        specialists = self._tiers_to_specialists(tiers_detected)
        tf_summary = self._summarize_terraform(terraform_payload)

        plan = AnalysisPlan(
            application_id=state.application_id,
            tiers_detected=tiers_detected,
            specialists_to_invoke=specialists,
            terraform_resources_summary=tf_summary,
            metadata_summary={
                "scenario_type": metadata.get("scenario_type"),
                "scenario_name": metadata.get("scenario_name"),
            },
            notes=None,
        )
        # Order matters: metadata first (it determines tier_topology,
        # the load-bearing input), terraform second (only feeds the
        # human-readable summary).
        evidence_refs = [metadata_obs_id, terraform_obs_id]
        return plan, evidence_refs

    def _fetch(
        self, tool_name: str, state: CycleState,
    ) -> tuple[dict[str, Any], int]:
        """Dispatch one read tool. Returns (observation_payload,
        observation_record_id). Raises if the harness rejects or the
        tool fails — both are unrecoverable for the System Mapper.

        The observation_record_id is the audit_records.id of the
        `observation` row this fetch produced. The caller uses it to
        populate `system_mapper_output.evidence_refs` so the conclusion
        cites the exact rows it was derived from."""
        result = dispatch_tool(
            self._store,
            self._action_harness,
            cycle_id=state.cycle_id,
            agent="system_mapper",
            tool_name=tool_name,
            arguments={"app_name": state.application_id},
        )
        if (not result.passed or result.observation is None
                or result.observation_record_id is None):
            raise SystemMapperError(
                f"System Mapper could not fetch {tool_name} for "
                f"{state.application_id}: {result.rejection_reason}"
            )
        return result.observation, result.observation_record_id

    # ----------------------------------------------------------------
    # Static helpers (pure functions; easy to unit-test directly)
    # ----------------------------------------------------------------
    @staticmethod
    def _extract_present_tiers(tier_topology: dict[str, Any]) -> list[Tier]:
        """Return tiers that the metadata marks as present.

        The dataset's tier_topology shape is:
          {"compute": {"present": True, ...} | None, "database": ..., ...}
        A value of None or a dict with present=False both count as absent.
        """
        out: list[Tier] = []
        for tier_name in ("compute", "database", "cache", "network"):
            entry = tier_topology.get(tier_name)
            if entry is None:
                continue
            if isinstance(entry, dict) and entry.get("present") is False:
                continue
            # Present (either explicit present=True or just a non-empty dict).
            out.append(tier_name)  # type: ignore[arg-type]
        return out

    @staticmethod
    def _tiers_to_specialists(tiers: list[Tier]) -> list[AgentName]:
        """Map tiers to specialist agents; deduplicate; preserve a
        deterministic order so the audit trail is stable."""
        seen: list[AgentName] = []
        for tier in tiers:
            specialist = TIER_TO_SPECIALIST.get(tier)
            if specialist is not None and specialist not in seen:
                seen.append(specialist)
        return seen

    @staticmethod
    def _summarize_terraform(payload: dict[str, Any]) -> str | None:
        """Short prose summary for the audit row. The full terraform
        is in the observation; this is just a glance-readable label."""
        terraform = payload.get("terraform")
        if not isinstance(terraform, str) or not terraform:
            return None
        # Count `resource "..."` declarations. Lightweight; not a real parse.
        resource_count = terraform.count('resource "')
        line_count = terraform.count("\n")
        return f"{resource_count} resource block(s), {line_count} terraform line(s)"

    # ----------------------------------------------------------------
    # Internal: write the system_mapper_output audit row
    # ----------------------------------------------------------------
    def _record_output(
        self,
        state: CycleState,
        plan: AnalysisPlan,
        evidence_refs: list[int],
    ) -> int:
        """Append the system_mapper_output row and return its id so the
        caller can stamp it on the state for downstream evidence-citing.

        `evidence_refs` cites the observation rows the conclusion was
        derived from — required and verified by the Reasoning Harness."""
        content = {
            "application_id": plan.application_id,
            "tiers_detected": list(plan.tiers_detected),
            "specialists_to_invoke": list(plan.specialists_to_invoke),
            "terraform_resources_summary": plan.terraform_resources_summary,
            "metadata_summary": dict(plan.metadata_summary),
            "notes": plan.notes,
            "evidence_refs": list(evidence_refs),
        }
        record = AuditRecord(
            cycle_id=state.cycle_id,
            parent_id=None,
            category="decision",
            type="system_mapper_output",
            agent="system_mapper",
            content=content,
        )
        return self._store.add_event(record)
