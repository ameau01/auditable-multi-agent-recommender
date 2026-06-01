"""Scope-catalog consistency tests.

The Action Harness will read SPECIALIST_TOOL_ALLOWLIST when it lands.
These tests catch drift between that map and the registered MCP tool
catalog at CI time, so a renamed tool or a deleted tier value can never
silently remove enforcement from a specialist.

Specifically the tests check:

  1. Every tool name listed in SPECIALIST_TOOL_ALLOWLIST is a registered
     FastMCP tool.
  2. Every tier value in every constraint is in the project's known
     tier set ({'compute', 'database', 'cache', 'network'}).
  3. Every registered tool that's intended for specialists appears in
     at least one allow-list. Tools that intentionally appear on zero
     specialists (e.g. get_handcrafted_recommendation, which is for the
     evaluator harness only) are documented in EXCLUSIONS below.
"""

from __future__ import annotations

import asyncio

import pytest

from src.mcp_server.scope import SPECIALIST_TOOL_ALLOWLIST
from src.mcp_server.server import mcp
from src.mcp_server._common import ALLOWED_TIERS


def _registered_tool_names() -> set[str]:
    """Return the set of tool names that FastMCP has registered.

    FastMCP's tool catalog is async on newer versions, sync on older;
    handle both. The list comes from importing the tool modules (which
    happens transitively via importing src.mcp_server.server).
    """
    tm = mcp._tool_manager
    tools = tm.list_tools()
    if asyncio.iscoroutine(tools):
        tools = asyncio.run(tools)
    return {t.name for t in tools}


# Tools that intentionally appear in no specialist's allow-list because
# they are not addressed to specialists. Update this set deliberately
# when adding a new tool of this kind.
EXCLUSIONS_FROM_SPECIALISTS: set[str] = set()


class TestScopeConsistency:
    def test_every_allowlist_name_is_a_registered_tool(self):
        registered = _registered_tool_names()
        for specialist, tools in SPECIALIST_TOOL_ALLOWLIST.items():
            for name in tools:
                assert name in registered, (
                    f"specialist {specialist!r} allow-lists {name!r}, but no "
                    f"such tool is registered with FastMCP. Registered "
                    f"tools: {sorted(registered)}"
                )

    def test_every_tier_constraint_is_a_valid_tier(self):
        allowed = set(ALLOWED_TIERS)
        for specialist, tools in SPECIALIST_TOOL_ALLOWLIST.items():
            for name, tier_constraint in tools.items():
                if tier_constraint is None:
                    continue
                for tier in tier_constraint:
                    assert tier in allowed, (
                        f"specialist {specialist!r} allows {name!r} on tier "
                        f"{tier!r}, but only {sorted(allowed)} are valid tiers"
                    )

    def test_every_registered_tool_appears_on_at_least_one_surface(self):
        """Catches the inverse drift: a new tool added to the catalog but
        forgotten in scope.py — a specialist somewhere should know about
        it, or it should be in EXCLUSIONS_FROM_SPECIALISTS.
        """
        registered = _registered_tool_names()
        # The evaluator_harness exists in the allow-list too; treat it as
        # a "specialist" for this consistency check.
        all_allowlisted: set[str] = set()
        for tools in SPECIALIST_TOOL_ALLOWLIST.values():
            all_allowlisted.update(tools.keys())
        orphans = registered - all_allowlisted - EXCLUSIONS_FROM_SPECIALISTS
        assert not orphans, (
            f"these registered tools appear on no specialist's allow-list "
            f"and are not in EXCLUSIONS_FROM_SPECIALISTS: {sorted(orphans)}. "
            f"Either add them to a specialist in scope.py or document the "
            f"exclusion in test_scope.py."
        )


class TestScopeRoles:
    """Sanity checks on the documented role boundaries.

    These are not just data-shape checks; they encode the architectural
    decisions in docs/agents.md and docs/mcp-server.md. If you change
    one of these, change the doc in the same commit.
    """

    @pytest.mark.parametrize("specialist", ["compute_analyst",
                                            "data_layer_analyst",
                                            "network_analyst",
                                            "system_mapper",
                                            "cross_tier_evaluator"])
    def test_no_specialist_sees_the_gold_answer(self, specialist):
        """Only the evaluator_harness gets get_handcrafted_recommendation."""
        tools = SPECIALIST_TOOL_ALLOWLIST[specialist]
        assert "get_handcrafted_recommendation" not in tools, (
            f"{specialist} must not have access to the gold answer — that "
            f"would let it reason backward from the target."
        )

    def test_compute_analyst_cannot_read_other_tiers(self):
        ca = SPECIALIST_TOOL_ALLOWLIST["compute_analyst"]
        for name, constraint in ca.items():
            if constraint is not None:
                assert constraint == ["compute"], (
                    f"compute_analyst's {name!r} allows tiers {constraint}, "
                    f"expected ['compute'] only"
                )

    def test_network_analyst_cannot_read_other_tiers(self):
        na = SPECIALIST_TOOL_ALLOWLIST["network_analyst"]
        for name, constraint in na.items():
            if constraint is not None:
                assert constraint == ["network"], (
                    f"network_analyst's {name!r} allows tiers {constraint}, "
                    f"expected ['network'] only"
                )

    def test_data_layer_analyst_owns_database_and_cache(self):
        dla = SPECIALIST_TOOL_ALLOWLIST["data_layer_analyst"]
        for name, constraint in dla.items():
            if constraint is not None:
                assert set(constraint) == {"database", "cache"}, (
                    f"data_layer_analyst's {name!r} allows tiers {constraint}, "
                    f"expected {{database, cache}}"
                )

    def test_cross_tier_evaluator_spans_all_tiers(self):
        cte = SPECIALIST_TOOL_ALLOWLIST["cross_tier_evaluator"]
        # The evaluator's tier-parameterized tools must include all 4 tiers.
        for name, constraint in cte.items():
            if constraint is not None:
                assert set(constraint) == set(ALLOWED_TIERS), (
                    f"cross_tier_evaluator's {name!r} allows {constraint}, "
                    f"expected all four tiers"
                )
