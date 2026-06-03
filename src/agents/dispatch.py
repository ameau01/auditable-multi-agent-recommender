"""dispatch_tool: the single place the substance-vs-enforcement chain runs.

Every MCP tool call an agent makes goes through this function. The
flow is:

  1. ActionHarness.check_tool_call(agent, tool_name, arguments)
     — writes a `tool_call_policy_check` row to harness_trail.
     — returns PolicyResult(passed=True/False, harness_record_id).

  2. If rejected: return early. No audit_records writes. The rejection
     lives only in harness_trail. This is the architectural property
     `docs/audit-trail.md` describes: the *absence* of a tool_call /
     observation pair is itself the audit signal that "this was
     attempted but not allowed."

  3. If passed: invoke the tool via the in-process MCP adapter, then
     write two audit_records rows:
        - `tool_call`   (the request the agent made; parameters echoed)
        - `observation` (the tool's response; the data the agent saw)
     Both carry the agent name. The tool_call row's id is stamped onto
     the harness verdict's `related_event_id` so the parallel-chain
     query (`get_harness_events_for_audit_record`) lights up.

  4. Return a ToolResult so the calling agent knows whether to use the
     observation or treat the call as failed.

This module's correctness is verified by tests/unit/agents/test_dispatch.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import update  # noqa: F401  -- used below

from ..audit.schema import harness_trail
from ..audit.store import AuditStore
from ..harnesses.action import ActionHarness, PolicyResult
from ..models.audit import AuditRecord
from ..models.enums import AgentName
from . import mcp_adapter


@dataclass
class ToolResult:
    """Outcome of one dispatch_tool call.

    When `passed=False`, `observation` is None and `rejection_reason`
    explains why. The caller MUST handle this — proceeding without
    the data is a logic error, not a tool error.
    """
    passed: bool
    observation: dict[str, Any] | None
    rejection_reason: str | None
    # Audit linkage. tool_call_record_id is the audit_records.id of the
    # tool_call row (or None on rejection). observation_record_id is
    # the audit_records.id of the observation row. harness_record_id
    # is the harness_trail row that gated this call (always present).
    tool_call_record_id: int | None
    observation_record_id: int | None
    harness_record_id: int


def dispatch_tool(
    store: AuditStore,
    action_harness: ActionHarness,
    *,
    cycle_id: str,
    agent: AgentName,
    tool_name: str,
    arguments: dict[str, Any],
    parent_record_id: int | None = None,
) -> ToolResult:
    """Gate, invoke, and record one tool call. See module docstring."""
    policy: PolicyResult = action_harness.check_tool_call(
        cycle_id=cycle_id,
        agent=agent,
        tool_name=tool_name,
        arguments=arguments,
    )

    # ----------------------------------------------------------------
    # Rejected path. Substance row never lands.
    # ----------------------------------------------------------------
    if not policy.passed:
        return ToolResult(
            passed=False,
            observation=None,
            rejection_reason=policy.rejection_reason,
            tool_call_record_id=None,
            observation_record_id=None,
            harness_record_id=policy.harness_record_id,
        )

    # ----------------------------------------------------------------
    # Allowed path. Write tool_call + observation, link to harness row.
    # ----------------------------------------------------------------
    tool_call_record = AuditRecord(
        cycle_id=cycle_id,
        parent_id=parent_record_id,
        category="evidence",
        type="tool_call",
        agent=agent,
        content={
            "tool_name": tool_name,
            "arguments": arguments,
        },
    )
    tool_call_id = store.add_event(tool_call_record)

    # Stamp the harness verdict's related_event_id to this tool_call.
    # This is what makes the substance ↔ enforcement chain queryable.
    with store.engine.begin() as conn:
        conn.execute(
            update(harness_trail)
            .where(harness_trail.c.id == policy.harness_record_id)
            .values(related_event_id=tool_call_id)
        )

    # Invoke the actual tool.
    try:
        result = mcp_adapter.call_tool(tool_name, arguments)
    except Exception as exc:  # noqa: BLE001
        # Tool blew up. Record the observation with the error so the
        # trail captures what actually happened. The caller decides
        # whether to retry / escalate.
        observation_record = AuditRecord(
            cycle_id=cycle_id,
            parent_id=tool_call_id,
            category="evidence",
            type="observation",
            agent=agent,
            content={
                "tool_name": tool_name,
                "result": {},
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        obs_id = store.add_event(observation_record)
        return ToolResult(
            passed=False,
            observation=None,
            rejection_reason=f"tool raised: {type(exc).__name__}",
            tool_call_record_id=tool_call_id,
            observation_record_id=obs_id,
            harness_record_id=policy.harness_record_id,
        )

    observation_record = AuditRecord(
        cycle_id=cycle_id,
        parent_id=tool_call_id,
        category="evidence",
        type="observation",
        agent=agent,
        content={
            "tool_name": tool_name,
            "result": result,
        },
    )
    obs_id = store.add_event(observation_record)

    return ToolResult(
        passed=True,
        observation=result,
        rejection_reason=None,
        tool_call_record_id=tool_call_id,
        observation_record_id=obs_id,
        harness_record_id=policy.harness_record_id,
    )
