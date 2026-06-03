"""Tests for the System Mapper node.

Covers:
  - tier_topology → tiers_detected extraction (full present, partial absent).
  - tiers_detected → specialists_to_invoke mapping with de-duplication.
  - terraform_resources_summary built from raw text.
  - End-to-end run produces an AnalysisPlan, writes an audit row.
  - SystemMapperError raised when a fetch is harness-rejected.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.analysis_plan import AnalysisPlan
from src.agents.state import CycleState
from src.agents.system_mapper import (
    SystemMapperError,
    SystemMapperNode,
    TIER_TO_SPECIALIST,
)
from src.audit import AuditStore
from src.audit.queries import get_cycle_events
from src.harnesses.action import ActionHarness
from src.harnesses.reasoning import ReasoningHarness


# ============================================================
# Pure helpers
# ============================================================
def test_extract_tiers_full_present() -> None:
    topology = {
        "compute":  {"present": True},
        "database": {"present": True},
        "cache":    None,
        "network":  {"present": True},
    }
    out = SystemMapperNode._extract_present_tiers(topology)
    assert out == ["compute", "database", "network"]  # cache absent, ordered


def test_extract_tiers_empty_dict() -> None:
    assert SystemMapperNode._extract_present_tiers({}) == []


def test_extract_tiers_explicit_present_false() -> None:
    """A tier with present=False is absent."""
    topology = {
        "compute":  {"present": True},
        "database": {"present": False},
    }
    assert SystemMapperNode._extract_present_tiers(topology) == ["compute"]


def test_tiers_to_specialists_dedupes_data_layer() -> None:
    """database + cache both map to data_layer_analyst. The plan
    must list data_layer_analyst once, in stable order."""
    out = SystemMapperNode._tiers_to_specialists(["compute", "database", "cache"])
    assert out == ["compute_analyst", "data_layer_analyst"]


def test_tiers_to_specialists_all_four() -> None:
    out = SystemMapperNode._tiers_to_specialists(
        ["compute", "database", "cache", "network"],
    )
    assert out == ["compute_analyst", "data_layer_analyst", "network_analyst"]


def test_tier_to_specialist_table_covers_every_tier() -> None:
    """Every value in the Tier enum has a specialist owner."""
    from src.models.enums import TIERS
    assert TIERS == set(TIER_TO_SPECIALIST.keys())


def test_summarize_terraform_counts_resources_and_lines() -> None:
    payload = {
        "terraform": (
            'resource "aws_launch_template" "compute" {}\n'
            'resource "aws_db_instance" "db" {}\n'
            'resource "aws_security_group_rule" "egress" {}\n'
        ),
    }
    summary = SystemMapperNode._summarize_terraform(payload)
    assert summary is not None
    assert "3 resource block(s)" in summary


def test_summarize_terraform_handles_empty() -> None:
    assert SystemMapperNode._summarize_terraform({"terraform": ""}) is None
    assert SystemMapperNode._summarize_terraform({}) is None


# ============================================================
# End-to-end node run
# ============================================================
def test_run_produces_plan_and_writes_audit_row(
    store: AuditStore,
    action_harness: ActionHarness,
    cycle_id: str,
    mock_mcp,
    app_08_metadata: dict[str, Any],
    app_08_terraform: dict[str, Any],
) -> None:
    mock_mcp.register("get_scenario_metadata", app_08_metadata)
    mock_mcp.register("get_terraform", app_08_terraform)

    node = SystemMapperNode(store, action_harness, ReasoningHarness(store))
    state = CycleState(application_id="app-08", cycle_id=cycle_id)
    update = node.run(state)

    plan = update["analysis_plan"]
    assert isinstance(plan, AnalysisPlan)
    assert plan.application_id == "app-08"
    assert plan.tiers_detected == ["compute", "database"]
    assert plan.specialists_to_invoke == ["compute_analyst", "data_layer_analyst"]
    assert plan.metadata_summary["scenario_type"] == "cross_tier_negative"
    assert plan.terraform_resources_summary is not None
    assert "2 resource block(s)" in plan.terraform_resources_summary

    events = get_cycle_events(store, cycle_id)
    sys_rows = [e for e in events if e.type == "system_mapper_output"]
    assert len(sys_rows) == 1
    assert sys_rows[0].agent == "system_mapper"
    assert sys_rows[0].content["tiers_detected"] == ["compute", "database"]


def test_run_unwraps_metadata_envelope(
    store: AuditStore,
    action_harness: ActionHarness,
    cycle_id: str,
    mock_mcp,
    app_08_terraform: dict[str, Any],
) -> None:
    """Regression: GetScenarioMetadataResponse wraps the actual metadata
    in a `metadata:` key alongside `app_name`. If the System Mapper
    reads tier_topology from the top level (forgetting the envelope),
    it sees nothing and produces an empty plan. This test pins the
    unwrap by passing an envelope where the only place tier_topology
    exists is inside `metadata` — a flat-read implementation would
    return tiers_detected=[]."""
    envelope = {
        "app_name": "app-08",
        "metadata": {
            "tier_topology": {
                "compute":  {"present": True},
                "database": {"present": True},
                "cache":    None,
                "network":  {"present": True},
            },
            "scenario_type": "cross_tier_negative",
        },
    }
    mock_mcp.register("get_scenario_metadata", envelope)
    mock_mcp.register("get_terraform", app_08_terraform)

    node = SystemMapperNode(store, action_harness, ReasoningHarness(store))
    state = CycleState(application_id="app-08", cycle_id=cycle_id)
    plan = node.run(state)["analysis_plan"]
    assert plan.tiers_detected == ["compute", "database", "network"]
    assert plan.specialists_to_invoke == [
        "compute_analyst", "data_layer_analyst", "network_analyst",
    ]
    assert plan.metadata_summary["scenario_type"] == "cross_tier_negative"


def test_run_raises_when_harness_rejects_fetch(
    store: AuditStore,
    action_harness: ActionHarness,
    cycle_id: str,
    mock_mcp,
) -> None:
    """Simulate a harness rejection by giving system_mapper a tool it
    cannot call. The Action Harness's allow-list for system_mapper
    only includes scenarios tools; calling get_top_queries triggers
    rejection (it's not on the allow-list)."""
    # The System Mapper agent itself only calls get_scenario_metadata and
    # get_terraform. We force a rejection by NOT registering those in the
    # mock; instead we register a different tool. The mock would only
    # raise if dispatch got past the harness — but the harness ALLOWS
    # both metadata + terraform for system_mapper, so we need a different
    # path. Simpler: monkey-patch scope.py for this test.
    from src.mcp_server import scope as scope_mod
    original = dict(scope_mod.SPECIALIST_TOOL_ALLOWLIST)
    # Strip system_mapper of the metadata tool to force a rejection.
    scope_mod.SPECIALIST_TOOL_ALLOWLIST["system_mapper"] = {}
    try:
        node = SystemMapperNode(store, action_harness, ReasoningHarness(store))
        state = CycleState(application_id="app-08", cycle_id=cycle_id)
        with pytest.raises(SystemMapperError, match="could not fetch"):
            node.run(state)
    finally:
        scope_mod.SPECIALIST_TOOL_ALLOWLIST.clear()
        scope_mod.SPECIALIST_TOOL_ALLOWLIST.update(original)
