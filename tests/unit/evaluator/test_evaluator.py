"""Unit tests for src/evaluator/evaluator.py (the Scorer class).

Construct the Scorer with crafted in-memory dicts (via the bare
constructor) or with tmp_path fixtures (for from_single_rules_file).
No reads from the real eval-set/ folder; that path is exercised by
tests/integration/.
"""

from __future__ import annotations

import json

import pytest

from src.evaluator import Scorer


# ============================================================
# Shared test data
# ============================================================
def _valid_rules() -> dict:
    return {
        "finding_type_allowed": ["issue_found"],
        "primary_tier_allowed": ["compute"],
        "secondary_tier_allowed": [None],
        "action_category_allowed": ["rightsizing"],
        "action_keyword_groups": [["downsize", "rightsize"], ["compute"]],
        "action_keyword_min_match": 2,
    }


def _valid_prediction() -> dict:
    return {
        "finding_type": "issue_found",
        "primary_tier": "compute",
        "secondary_tier": None,
        "action_category": "rightsizing",
        "specific_change": "downsize the compute instances to t3.medium",
        "reasoning": "compute is over-provisioned",
        "evidence": {
            "telemetry_observations": ["cpu p95 30%", "ram p95 45%", "load low"],
            "infrastructure_context": [],
            "correlation_observations": [],
        },
        "cost_impact": {"savings_monthly_usd": 500},
        "projected_state": {"cpu_p95_estimate": 60},
    }


# ============================================================
# Construction
# ============================================================
class TestEvaluatorConstruction:
    def test_bare_constructor_accepts_rules_dict(self):
        e = Scorer(rules_by_sid={"08": _valid_rules()})
        assert e.scenario_ids == ["08"]

    def test_bare_constructor_accepts_metadata_dict(self):
        meta = {"08": {"scenario_specific_evidence": {}}}
        e = Scorer(
            rules_by_sid={"08": _valid_rules()},
            metadata_by_sid=meta,
        )
        assert e.metadata_for("08") == meta["08"]

    def test_from_single_rules_file_loads_one_scenario(self, tmp_path):
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(_valid_rules()))
        e = Scorer.from_single_rules_file(rules_path, sid="99")
        assert e.scenario_ids == ["99"]
        assert e.rules_for("99")["primary_tier_allowed"] == ["compute"]


# ============================================================
# Lookup methods
# ============================================================
class TestEvaluatorLookup:
    def test_rules_for_known_sid(self):
        e = Scorer(rules_by_sid={"08": _valid_rules()})
        rules = e.rules_for("08")
        assert "finding_type_allowed" in rules

    def test_rules_for_unknown_sid_raises_keyerror(self):
        e = Scorer(rules_by_sid={"08": _valid_rules()})
        with pytest.raises(KeyError) as exc_info:
            e.rules_for("99")
        assert "99" in str(exc_info.value)

    def test_metadata_for_returns_none_when_not_loaded(self):
        e = Scorer(rules_by_sid={"08": _valid_rules()})
        assert e.metadata_for("08") is None

    def test_scenario_ids_returns_sorted_list(self):
        e = Scorer(rules_by_sid={
            "08": _valid_rules(),
            "01": _valid_rules(),
            "15": _valid_rules(),
        })
        assert e.scenario_ids == ["01", "08", "15"]


# ============================================================
# Scoring + gate semantics
# ============================================================
class TestEvaluatorScoreOne:
    def test_score_one_returns_dict_with_all_layers(self):
        e = Scorer(rules_by_sid={"08": _valid_rules()})
        result = e.score_one("08", _valid_prediction())
        assert set(result.keys()) >= {"shape", "correctness", "floor", "mid", "rich"}

    def test_score_one_gold_passes_every_layer(self):
        e = Scorer(rules_by_sid={"08": _valid_rules()})
        result = e.score_one("08", _valid_prediction())
        assert result["shape"].passed
        assert result["correctness"].passed
        assert result["mid"].passed
        assert result["rich"].passed

    def test_score_one_skips_mid_and_rich_when_correctness_fails(self):
        e = Scorer(rules_by_sid={"08": _valid_rules()})
        bad = _valid_prediction()
        bad["primary_tier"] = "database"  # wrong: rules say compute
        result = e.score_one("08", bad)
        assert not result["correctness"].passed
        # Gate semantics: Mid and Rich are the literal string "skipped"
        assert result["mid"] == "skipped"
        assert result["rich"] == "skipped"


# ============================================================
# score_all batch interface
# ============================================================
class TestEvaluatorScoreAll:
    def test_score_all_returns_one_entry_per_scenario(self):
        e = Scorer(rules_by_sid={
            "08": _valid_rules(),
            "01": _valid_rules(),
        })
        predictions = {
            "08": _valid_prediction(),
            "01": _valid_prediction(),
        }
        results = e.score_all(predictions)
        assert set(results.keys()) == {"01", "08"}

    def test_score_all_reports_missing_prediction(self):
        e = Scorer(rules_by_sid={
            "08": _valid_rules(),
            "01": _valid_rules(),
        })
        results = e.score_all({"08": _valid_prediction()})  # 01 missing
        assert results["01"] == {"error": "no prediction submitted"}
        assert "shape" in results["08"]  # 08 still scored
