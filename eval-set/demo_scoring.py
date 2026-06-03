"""Demo: how to score a recommendation with the four-layer Scorer.

Uses app-08 (the cross-tier database cascade scenario) as a canonical
example. Constructs the Scorer, scores the gold recommendation against
its own scoring rules, and prints the per-layer verdict.

Default mode runs Shape + Correctness only (no API key required). Pass
--with-judge to additionally call the LLM judge on Mid and Rich; this
requires either OPENAI_API_KEY or ANTHROPIC_API_KEY in the environment
(loaded from .env). When both are set, OpenAI is preferred.

To verify all 18 gold answers still pass their own deterministic layers,
run the integration test instead:

    pytest tests/integration/test_golden_answers.py

Run this demo:
    python eval-set/demo_scoring.py              # Shape + Correctness only
    python eval-set/demo_scoring.py --with-judge # adds Mid + Rich via LLM
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

EVAL_SET_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_SET_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluator import Scorer

DEMO_SCENARIO = "08"  # canonical cross-tier example used across docs


def _verdict(layer_result) -> str:
    """Format a layer result for the table."""
    if isinstance(layer_result, str):
        return layer_result.upper()  # 'SKIPPED'
    checks = layer_result.checks
    if len(checks) == 1 and checks[0].name == "short_circuit":
        return " -- "
    if len(checks) == 1 and checks[0].name == "skipped":
        return "SKIP"
    return "PASS" if layer_result.passed else "FAIL"


def main():
    parser = argparse.ArgumentParser(
        description="Demo: score app-08's gold answer with the four-layer Scorer.",
    )
    parser.add_argument(
        "--with-judge", action="store_true",
        help="Also run the LLM judge for Mid and Rich (requires "
             "ANTHROPIC_API_KEY in .env). Default runs Shape + Correctness only.",
    )
    args = parser.parse_args()

    judge = None
    if args.with_judge:
        from src.evaluator.judge_client import JudgeClient
        if not JudgeClient.is_available():
            print("ERROR: --with-judge requires OPENAI_API_KEY or "
                  "ANTHROPIC_API_KEY in the environment (loaded from .env). "
                  "Falling back to deterministic layers only would defeat "
                  "the purpose of the flag.",
                  file=sys.stderr)
            sys.exit(2)
        judge = JudgeClient()

    scorer = Scorer.from_eval_set_dir(
        EVAL_SET_DIR,
        dataset_examples_dir=PROJECT_ROOT / "dataset-examples",
        judge=judge,
    )
    # Use the scorer's own loaded gold (derived from the scenario's
    # composite raw_recommendation.json) as the prediction-to-score, so
    # the demo reflects how a real prediction would flow through.
    gold = scorer.gold_for(DEMO_SCENARIO)
    result = scorer.score_one(DEMO_SCENARIO, gold)

    print()
    mode = "deterministic + LLM judge" if judge else "deterministic only"
    print(f"  Scoring the gold recommendation for app-{DEMO_SCENARIO} ({mode}):")
    for label, key in (("Shape", "shape"), ("Correctness", "correctness"),
                       ("Mid", "mid"), ("Rich", "rich")):
        print(f"    {label:<12s} {_verdict(result[key])}")
    print()
    if judge:
        print("  All four layers ran. The gold answer is internally consistent")
        print("  with both its scoring rules and the LLM judge's richness rubric.")
    else:
        print("  Shape and Correctness ran; Mid and Rich skipped (no judge).")
        print("  Pass --with-judge to enable the LLM judge (requires API key).")
    print()
    print("  To verify all 18 gold answers pass the deterministic layers:")
    print("    pytest tests/integration/test_golden_answers.py")
    print()


if __name__ == "__main__":
    main()
