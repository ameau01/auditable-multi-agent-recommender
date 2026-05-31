"""Sample tests for the vendored scenario 08.

These tests show how to load the scenario files and assert on the gold
answer. Use them as a template when writing tests for the agent system.

Run:
    pytest dataset-examples/tests/
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenario_08"


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture(scope="module")
def metadata() -> dict:
    return json.loads((SCENARIO_DIR / "metadata.json").read_text())


@pytest.fixture(scope="module")
def gold() -> dict:
    return json.loads(
        (SCENARIO_DIR / "handcrafted_recommendation.json").read_text()
    )


@pytest.fixture(scope="module")
def correlation() -> list:
    return json.loads((SCENARIO_DIR / "correlation_evidence.json").read_text())


# ============================================================
# Tests on file presence and basic shape
# ============================================================
def test_all_eight_scenario_files_present():
    expected = {
        "metadata.json",
        "main.tf",
        "compute_telemetry.json",
        "database_telemetry.json",
        "cache_telemetry.json",
        "network_telemetry.json",
        "correlation_evidence.json",
        "handcrafted_recommendation.json",
    }
    found = {p.name for p in SCENARIO_DIR.iterdir() if p.is_file()}
    # Allow extra files like README.md alongside the data files.
    missing = expected - found
    assert not missing, f"missing scenario files: {sorted(missing)}"


def test_all_json_files_parse():
    for fname in (
        "metadata.json",
        "compute_telemetry.json",
        "database_telemetry.json",
        "cache_telemetry.json",
        "network_telemetry.json",
        "correlation_evidence.json",
        "handcrafted_recommendation.json",
    ):
        path = SCENARIO_DIR / fname
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as e:
            pytest.fail(f"{fname} does not parse: {e}")


def test_terraform_file_is_not_empty():
    tf = (SCENARIO_DIR / "main.tf").read_text()
    assert len(tf) > 100, "main.tf is suspiciously short"


# ============================================================
# Tests on scenario metadata
# ============================================================
def test_metadata_identifies_scenario_08(metadata):
    assert metadata["scenario_id"] == "08"
    assert metadata["scenario_name"] == "Database Bottleneck Impact"
    assert metadata["scenario_type"] == "cross_tier_negative"


def test_metadata_has_top_queries_fixture(metadata):
    fixtures = metadata.get("scenario_specific_evidence", {})
    queries = fixtures.get("top_queries", [])
    assert len(queries) >= 5, "scenario 08 should ship at least 5 top queries"
    for q in queries:
        assert "query_text" in q
        assert "count" in q
        assert "p95_latency_ms" in q


# ============================================================
# Tests on the gold answer shape and content
# ============================================================
def test_gold_finding_type(gold):
    assert gold["finding_type"] == "issue_found"


def test_gold_primary_and_secondary_tier(gold):
    assert gold["primary_tier"] == "database"
    assert gold["secondary_tier"] == "compute"


def test_gold_action_category(gold):
    assert gold["action_category"] == "query_cache_optimization"


def test_gold_specific_change_mentions_indexes(gold):
    text = gold["specific_change"].lower()
    assert "index" in text, (
        "scenario 08 gold should propose indexes on the slow queries"
    )
    assert "replica" in text, (
        "scenario 08 gold should propose adding read replicas"
    )


def test_gold_cites_both_tiers_in_evidence(gold):
    # Multi-tier scenarios require the recommendation text to mention
    # both the database (primary) and compute (secondary) tiers somewhere
    # in evidence or reasoning.
    combined = (
        gold.get("specific_change", "")
        + " "
        + gold.get("reasoning", "")
    ).lower()
    for category in (
        "telemetry_observations",
        "infrastructure_context",
        "correlation_observations",
    ):
        for bullet in (gold.get("evidence", {}) or {}).get(category) or []:
            combined += " " + bullet.lower()

    assert "database" in combined, "expected 'database' in gold evidence/reasoning"
    assert "compute" in combined, "expected 'compute' in gold evidence/reasoning"


def test_gold_cost_impact_is_negative_savings(gold):
    # Scenario 08 is a reliability fix that costs more. Savings are negative.
    savings = (gold.get("cost_impact") or {}).get("savings_monthly_usd")
    assert savings is not None
    assert savings < 0, (
        "scenario 08 trades cost for SLA, so savings_monthly_usd should be negative"
    )


# ============================================================
# Tests on the cross-tier correlation
# ============================================================
def test_correlation_evidence_links_database_to_compute(correlation):
    assert len(correlation) >= 1, "scenario 08 should have at least 1 correlation"
    pair = correlation[0]
    assert pair["tier_a"] == "database"
    assert pair["tier_b"] == "compute"


def test_correlation_is_strong_with_positive_lag(correlation):
    pair = correlation[0]
    assert pair["coefficient"] >= 0.9, "correlation should be strong (>= 0.9)"
    assert pair["lag_minutes"] > 0, (
        "scenario 08 expects database latency to lead compute latency"
    )
