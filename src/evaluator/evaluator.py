"""Scorer class: stateful wrapper around the four-layer evaluator.

Loads gold answers + scoring rules + (optional) judge client once at init,
then scores any number of predictions. Implements the gate semantics: when
Correctness fails, Mid and Rich are reported as 'skipped' rather than 'fail'.

The name is deliberately Scorer (not Evaluator) so the four-layer scoring
class is unambiguous against the Cross-Tier Evaluator (the agent that
synthesizes specialist findings into a Composite). One operates on a
finished Composite to grade it; the other produces the Composite.

Typical use:

    from src.evaluator import Scorer
    from src.evaluator.judge_client import JudgeClient

    s = Scorer.from_eval_set_dir(
        "eval-set/",
        dataset_examples_dir="dataset-examples/",
        judge=JudgeClient(),  # None for deterministic-only scoring
    )
    result = s.score_one("08", prediction_dict)
    # result = {"shape": TierResult, "correctness": TierResult,
    #           "mid": TierResult | "skipped",
    #           "rich": TierResult | "skipped"}

    all_results = s.score_all(predictions_dict)
    # all_results = {"01": {...}, "02": {...}, ...}

The Scorer can also be built from a single rules file for ad-hoc
scoring of one scenario:

    s = Scorer.from_single_rules_file(
        "path/to/rules.json", sid="99",
        gold=gold_dict, metadata=metadata_dict, judge=None,
    )

Threshold-gating design (per docs/eval-set.md):
- When a non-short-circuit scenario is correct, the judge is called ONCE
  per scenario; the resulting score is shared by Mid and Rich.
- Mid passes if score >= MID_THRESHOLD (currently 30).
- Rich passes if score >= RICH_THRESHOLD (currently 60) AND the four
  deterministic structural checks pass.
- When no judge is configured (judge=None), Mid and Rich return
  'skipped' verdicts; the report-format contract holds either way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models.composite import Composite
from .correctness_measure import score_correctness, score_floor
from .mid_measure import score_mid
from .richness_measure import score_rich
from .rules import load_rules_file, validate_rules
from .shape_measure import score_shape


class Scorer:
    """Stateful four-layer scorer with pre-loaded rules.

    Construct via the from_* class methods, not the bare constructor.

    Naming note: "Scorer" rather than "Evaluator" so this class doesn't
    clash with the Cross-Tier Evaluator agent (which synthesizes specialist
    findings into a Composite). They sit on opposite sides of the pipeline.
    """

    def __init__(self,
                 rules_by_sid: dict[str, dict],
                 gold_by_sid: dict[str, dict] | None = None,
                 metadata_by_sid: dict[str, dict] | None = None,
                 judge: Any | None = None):
        """Internal constructor; prefer from_eval_set_dir or from_single_rules_file.

        Args:
            rules_by_sid: per-scenario scoring rules (the Correctness +
                short_circuit + must_cite_fixture config).
            gold_by_sid: per-scenario gold answers. Needed by the judge
                to compare prediction.specific_change against gold.
                When absent, Mid + Rich fall back to 'skipped'.
            metadata_by_sid: per-scenario dataset metadata. Needed by
                Rich's fixture_citation structural check.
            judge: an LLM judge instance (typically JudgeClient). When
                None, Mid + Rich skip gracefully.
        """
        self._rules: dict[str, dict] = rules_by_sid
        self._gold: dict[str, dict] = gold_by_sid or {}
        self._metadata: dict[str, dict] = metadata_by_sid or {}
        self._judge = judge

    # ============================================================
    # Constructors
    # ============================================================
    @classmethod
    def from_eval_set_dir(cls,
                          eval_set_dir: Path | str,
                          dataset_examples_dir: Path | str | None = None,
                          judge: Any | None = None) -> "Scorer":
        """Load all scenarios' composites from eval-set/expectations/NN/.

        Each composite (raw_recommendation.json) carries both the gold
        answer (top-level prediction fields) and the scoring rubric
        (scoring_metadata block) in a single file. The Scorer splits
        them back into rules_by_sid + gold_by_sid via the composite's
        to_rules_dict()/to_gold_dict() accessors so downstream measure
        modules keep their existing key access patterns.

        Args:
            eval_set_dir: path to eval-set/ (contains expectations/NN/
                folders, each with raw_recommendation.json).
            dataset_examples_dir: optional path to dataset-examples/
                (for scenario_NN/metadata.json used by Rich).
            judge: optional LLM judge. When None, Mid + Rich skip.
        """
        eval_set_dir = Path(eval_set_dir)
        expectations_dir = eval_set_dir / "expectations"
        if not expectations_dir.exists():
            raise FileNotFoundError(
                f"expectations directory not found: {expectations_dir}"
            )

        rules: dict[str, dict] = {}
        gold: dict[str, dict] = {}
        for sid_dir in sorted(expectations_dir.iterdir()):
            if not sid_dir.is_dir():
                continue
            sid = sid_dir.name
            comp_path = sid_dir / "raw_recommendation.json"
            if not comp_path.exists():
                continue
            composite = Composite.model_validate_json(comp_path.read_text())
            scenario_rules = composite.to_rules_dict()
            validate_rules(scenario_rules, source=str(comp_path))
            rules[sid] = scenario_rules
            gold[sid] = composite.to_gold_dict()

        if not rules:
            raise FileNotFoundError(
                f"no NN/raw_recommendation.json composites found under "
                f"{expectations_dir}"
            )

        # Load scenario metadata if a dataset-examples folder is provided
        metadata: dict[str, dict] = {}
        if dataset_examples_dir is not None:
            dataset_examples_dir = Path(dataset_examples_dir)
            for sid in rules.keys():
                p = dataset_examples_dir / f"scenario_{sid}" / "metadata.json"
                if p.exists():
                    metadata[sid] = json.loads(p.read_text())

        return cls(
            rules_by_sid=rules,
            gold_by_sid=gold,
            metadata_by_sid=metadata,
            judge=judge,
        )

    @classmethod
    def from_single_composite_file(cls,
                                   composite_path: Path | str,
                                   sid: str | None = None,
                                   metadata: dict | None = None,
                                   judge: Any | None = None) -> "Scorer":
        """Build an evaluator from a single raw_recommendation.json composite.

        Args:
            composite_path: path to a composite JSON file.
            sid: scenario id to register the loaded data under. When None,
                the composite's own scenario_id field is used.
            metadata: optional dataset metadata for the scenario.
            judge: optional LLM judge.
        """
        composite_path = Path(composite_path)
        composite = Composite.model_validate_json(composite_path.read_text())
        scenario_rules = composite.to_rules_dict()
        validate_rules(scenario_rules, source=str(composite_path))
        scenario_gold = composite.to_gold_dict()
        registered_sid = sid if sid is not None else composite.scenario_id
        return cls(
            rules_by_sid={registered_sid: scenario_rules},
            gold_by_sid={registered_sid: scenario_gold},
            metadata_by_sid={registered_sid: metadata} if metadata else {},
            judge=judge,
        )

    @classmethod
    def from_single_rules_file(cls,
                                rules_path: Path | str,
                                sid: str,
                                gold: dict | None = None,
                                metadata: dict | None = None,
                                judge: Any | None = None) -> "Scorer":
        """Build an evaluator that knows about exactly one scenario.

        Useful for ad-hoc scoring of a single new scenario without setting
        up the full eval-set directory.
        """
        rules = load_rules_file(rules_path)
        gold_by_sid = {sid: gold} if gold is not None else {}
        metadata_by_sid = {sid: metadata} if metadata is not None else {}
        return cls(
            rules_by_sid={sid: rules},
            gold_by_sid=gold_by_sid,
            metadata_by_sid=metadata_by_sid,
            judge=judge,
        )

    # ============================================================
    # Public API
    # ============================================================
    @property
    def scenario_ids(self) -> list[str]:
        return sorted(self._rules.keys())

    @property
    def has_judge(self) -> bool:
        """True if a judge is configured (Mid + Rich will run)."""
        return self._judge is not None

    def rules_for(self, sid: str) -> dict:
        """Get the loaded rules for a scenario."""
        if sid not in self._rules:
            raise KeyError(f"no rules loaded for scenario {sid!r}. "
                           f"Loaded: {self.scenario_ids}")
        return self._rules[sid]

    def gold_for(self, sid: str) -> dict | None:
        """Get the loaded gold answer for a scenario, or None if not loaded."""
        return self._gold.get(sid)

    def metadata_for(self, sid: str) -> dict | None:
        """Get the loaded metadata for a scenario, or None if not loaded."""
        return self._metadata.get(sid)

    def score_one(self, sid: str, prediction: dict) -> dict:
        """Score one prediction. Returns a dict of layer -> TierResult.

        Gate semantics:
        - Correctness fails -> Mid + Rich are 'skipped' (string).
        - Correctness passes + judge is None -> Mid + Rich run with
          judge_result=None and produce 'skipped' markers internally.
        - Correctness passes + judge is set + scenario is short-circuited
          (no-action finding_type) -> Mid + Rich return their
          short_circuit markers; no judge call is made.
        - Correctness passes + judge is set + scenario is action -> one
          judge call is made; the score is shared by Mid and Rich.
        """
        rules = self.rules_for(sid)
        metadata = self.metadata_for(sid)
        gold = self.gold_for(sid)

        shape = score_shape(prediction, rules)
        correctness = score_correctness(prediction, rules)
        floor = score_floor(prediction, rules)  # back-compat alias

        if not correctness.passed:
            return {
                "shape": shape,
                "correctness": correctness,
                "floor": floor,
                "mid": "skipped",
                "rich": "skipped",
            }

        # Decide whether to call the judge. Skip when:
        # - no judge configured, or
        # - this scenario is short-circuited (mid/rich will short-circuit
        #   without consulting the judge), or
        # - no gold loaded (judge has nothing to compare against).
        judge_result = self._maybe_score_judge(prediction, rules, gold)

        mid = score_mid(prediction, rules, judge_result=judge_result)
        rich = score_rich(prediction, rules, metadata,
                          judge_result=judge_result)

        return {
            "shape": shape,
            "correctness": correctness,
            "floor": floor,
            "mid": mid,
            "rich": rich,
        }

    def score_all(self, predictions: dict[str, dict]) -> dict[str, dict]:
        """Score a dict of {scenario_id: prediction} against all loaded rules.

        Returns {scenario_id: layer_results} per score_one. Scenarios in
        the evaluator that are missing from `predictions` get a special
        {'error': 'no prediction submitted'} entry.
        """
        results: dict[str, dict] = {}
        for sid in self.scenario_ids:
            if sid not in predictions:
                results[sid] = {"error": "no prediction submitted"}
            else:
                results[sid] = self.score_one(sid, predictions[sid])
        return results

    # ============================================================
    # Private
    # ============================================================
    def _maybe_score_judge(self,
                           prediction: dict,
                           rules: dict,
                           gold: dict | None) -> dict | None:
        """Call the judge if appropriate; otherwise return None.

        Returns None when any of these holds:
          - No judge is configured.
          - The scenario is short-circuited (no-action finding_type;
            mid/rich will short-circuit independently).
          - No gold is loaded for the scenario.
          - The prediction itself is short-circuited (its own
            finding_type is in NO_ACTION_FINDINGS).
        """
        if self._judge is None:
            return None
        if gold is None:
            return None

        # Avoid calling the judge for short-circuit scenarios; the
        # measure modules will return their own short_circuit markers.
        from .enums import NO_ACTION_FINDINGS
        if prediction.get("finding_type") in NO_ACTION_FINDINGS:
            return None
        if gold.get("finding_type") in NO_ACTION_FINDINGS:
            return None
        # short_circuit flag in rules also signals "no judge needed"
        sc = rules.get("short_circuit")
        if isinstance(sc, dict) and sc.get("applies"):
            return None

        return self._judge.score(gold, prediction)
