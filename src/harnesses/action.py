"""Action Harness — scopes tool calls and gates the final recommendation.

Two responsibilities, mirroring docs/harnesses.md §3:

  (1) `check_tool_call` — per-tool-call policy check. Consults the
      `SPECIALIST_TOOL_ALLOWLIST` in `src/mcp_server/scope.py` (the
      single source of truth for which agent can call which tool with
      which tier argument), records a `harness_trail` row with the
      verdict, and returns a `PolicyResult`. The caller (the agent's
      tool-dispatch shim) is expected to actually block the call when
      `passed=False`.

  (2) `check_recommendation_gate` — the final-recommendation gate that
      runs after the Cross-Tier Evaluator synthesizes. Declared here
      so the wiring exists; implementation lands in the next phase
      (Phase 11) once the recommendation pipeline is connected end-to-end.

Substance-vs-enforcement reminder: when `check_tool_call` returns
`passed=True`, the caller proceeds to invoke the MCP tool and writes
`tool_call` + `observation` rows into `audit_records` — `related_event_id`
on the harness row points to the audit `tool_call.id`. When it returns
`passed=False`, the caller does NOT write into `audit_records` — the
rejection lives only here in `harness_trail`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..audit.store import AuditStore
from ..mcp_server.scope import SPECIALIST_TOOL_ALLOWLIST
from ..models.audit import HarnessRecord
from ..models.enums import AgentName, Tier, Verdict


@dataclass
class PolicyResult:
    """Outcome of a tool-call policy check.

    Always carries `harness_record_id` so the caller (the dispatch
    layer) can stamp `related_event_id` on the audit `tool_call` row
    that follows on a passed result.
    """
    passed: bool
    verdict: Verdict
    harness_record_id: int
    rejection_reason: str | None = None


@dataclass
class GateResult:
    """Outcome of the final-recommendation gate (Phase 11)."""
    passed: bool
    verdict: Verdict
    harness_record_id: int
    severity_classification: str | None = None
    rejection_reason: str | None = None


class ActionHarness:
    """Per-tool-call gating + the final-recommendation gate.

    Construct with an `AuditStore`. Reuse across cycles.
    """

    def __init__(self, store: AuditStore) -> None:
        self._store = store

    # ----------------------------------------------------------------
    # (1) Per-tool-call policy check
    # ----------------------------------------------------------------
    def check_tool_call(
        self,
        cycle_id: str,
        agent: AgentName,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> PolicyResult:
        """Return whether `agent` may call `tool_name` with `arguments`.

        Two reasons a call is rejected:
          a) `agent` is not in the allow-list at all (unknown agent), or
             `tool_name` is not in this agent's allow-list (out of scope).
          b) `tool_name` takes a `tier` argument and the value provided
             is not in the allowed-tiers list for this (agent, tool) pair.

        Both produce verdict='rejected' with a `rejection_reason` that
        is human-readable and is also captured in the harness_trail row's
        content for later audit.
        """
        args = arguments or {}
        agent_table = SPECIALIST_TOOL_ALLOWLIST.get(agent)

        if agent_table is None:
            return self.route_policy_check(
                cycle_id=cycle_id,
                agent=agent,
                tool_name=tool_name,
                arguments_snapshot=args,
                tier_scope=None,
                verdict="rejected",
                rejection_reason=(
                    f"agent {agent!r} is not registered in "
                    "SPECIALIST_TOOL_ALLOWLIST; no tools permitted."
                ),
            )

        if tool_name not in agent_table:
            return self.route_policy_check(
                cycle_id=cycle_id,
                agent=agent,
                tool_name=tool_name,
                arguments_snapshot=args,
                tier_scope=None,
                verdict="rejected",
                rejection_reason=(
                    f"tool {tool_name!r} is not in agent {agent!r}'s "
                    "allow-list."
                ),
            )

        allowed_tiers = agent_table[tool_name]
        requested_tier = args.get("tier")
        if allowed_tiers is not None:
            # Tool takes a tier argument; check the value against the
            # allow-list for this (agent, tool) pair.
            if requested_tier is None:
                return self.route_policy_check(
                    cycle_id=cycle_id,
                    agent=agent,
                    tool_name=tool_name,
                    arguments_snapshot=args,
                    tier_scope=None,
                    verdict="rejected",
                    rejection_reason=(
                        f"tool {tool_name!r} requires a `tier` argument; "
                        f"none provided. Allowed: {allowed_tiers}."
                    ),
                )
            if requested_tier not in allowed_tiers:
                return self.route_policy_check(
                    cycle_id=cycle_id,
                    agent=agent,
                    tool_name=tool_name,
                    arguments_snapshot=args,
                    tier_scope=requested_tier,
                    verdict="rejected",
                    rejection_reason=(
                        f"agent {agent!r} may not call {tool_name!r} with "
                        f"tier={requested_tier!r}; allowed tiers are "
                        f"{allowed_tiers}."
                    ),
                )

        # All checks passed.
        return self.route_policy_check(
            cycle_id=cycle_id,
            agent=agent,
            tool_name=tool_name,
            arguments_snapshot=args,
            tier_scope=requested_tier,
            verdict="passed",
            rejection_reason=None,
        )

    # ----------------------------------------------------------------
    # (2) Final-recommendation gate (declared; full impl in next phase)
    # ----------------------------------------------------------------
    def check_recommendation_gate(
        self,
        cycle_id: str,
        recommendation_record_id: int,
        recommendation_content: dict[str, Any],
    ) -> GateResult:
        """Run the final-recommendation gate.

        Phase 11 will implement the four sub-checks (well-formedness,
        evidence completeness, severity classification, duplication).
        The signature is fixed so the orchestrator can wire it now and
        the body lands later.
        """
        raise NotImplementedError(
            "check_recommendation_gate lands in the next phase, once "
            "the orchestrated review pipeline produces recommendations "
            "end-to-end. See docs/harnesses.md §3."
        )

    # ----------------------------------------------------------------
    # Public: route the policy-check verdict and return the result
    # ----------------------------------------------------------------
    # Public on purpose, matching ReasoningHarness.route and
    # InputHarness.route. Callers route a verdict through this method
    # so it lands as a harness_trail row with the appropriate enforcement
    # type. "Route" matches LangGraph vocabulary — moving a verdict from
    # one node to the next — rather than "emit," which in LangGraph
    # specifically denotes streaming Pregel events.
    def route_policy_check(
        self,
        cycle_id: str,
        agent: AgentName,
        tool_name: str,
        arguments_snapshot: dict[str, Any],
        tier_scope: Tier | None,
        verdict: Verdict,
        rejection_reason: str | None,
    ) -> PolicyResult:
        record = HarnessRecord(
            cycle_id=cycle_id,
            parent_id=None,
            related_event_id=None,   # stamped by the caller post-tool-call
            harness="action",
            type="tool_call_policy_check",
            verdict=verdict,
            content={
                "agent": agent,
                "tool_name": tool_name,
                "tier_scope": tier_scope,
                "arguments_snapshot": arguments_snapshot,
                "rejection_reason": rejection_reason,
            },
        )
        rid = self._store.add_harness_event(record)
        return PolicyResult(
            passed=(verdict == "passed"),
            verdict=verdict,
            harness_record_id=rid,
            rejection_reason=rejection_reason,
        )
