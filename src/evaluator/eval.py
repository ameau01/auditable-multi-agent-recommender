"""Score one prediction for one named app against the eval-set's rules.

Simple CLI. Two flags, four-layer output, three exit codes.

Usage:
    python -m src.evaluator.eval --app-name app-08 --prediction my_pred.json

Default behavior: if either OPENAI_API_KEY or ANTHROPIC_API_KEY is in
the environment (loaded from .env), the LLM judge is enabled for Mid
and Rich. If neither key is set, Mid and Rich return '(skipped)'
markers and only Shape + Correctness are evaluated. Pass --no-judge to
disable the judge even when a key is set.

When both keys are set, OpenAI is used by default. Override with
LLM_JUDGE_PROVIDER=anthropic in .env, or LLM_JUDGE_MODEL=<model> to
pin a specific model name.

App-name convention. The CLI uses `app-NN` to match the MCP server's
naming (see docs/mcp-server.md). Internally, app-NN maps to the
two-digit scenario id NN used in eval-set/expectations/NN/.

Bulk scoring (all 18 apps at once) is not exposed through this CLI.
For that, use eval-set/demo_scoring.py or run pytest against
tests/integration/test_golden_answers.py.

Exit codes:
    0 - every layer that ran passed (including layers that gracefully
        skipped when no judge was available)
    1 - at least one layer that ran actually failed
    2 - usage error (missing files, malformed JSON, unknown app)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .evaluator import Scorer


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_EVAL_SET_DIR = PROJECT_ROOT / "eval-set"
DEFAULT_DATASET_EXAMPLES_DIR = PROJECT_ROOT / "dataset-examples"

# Accept app-NN where NN is two digits (matches mcp-server.md convention)
_APP_NAME_RE = re.compile(r"^app-(\d{2})$")


def _app_name_to_scenario_id(app_name: str) -> str:
    """Map an app-name like 'app-08' to the dataset scenario id '08'."""
    match = _APP_NAME_RE.match(app_name)
    if not match:
        raise ValueError(
            f"app-name must look like 'app-NN' (e.g. 'app-08'). Got: {app_name!r}"
        )
    return match.group(1)


def _is_gracefully_skipped(layer_result) -> bool:
    """True if this TierResult is a graceful skip (judge unavailable or
    no-action short-circuit). Both produce a passed=True TierResult with
    a single 'skipped' or 'short_circuit' check."""
    if isinstance(layer_result, str):
        return False  # literal 'skipped' means correctness-gated
    if len(layer_result.checks) != 1:
        return False
    return layer_result.checks[0].name in ("skipped", "short_circuit")


def _verdict_label(layer_result) -> str:
    """Format a per-layer result for the table output."""
    if isinstance(layer_result, str) and layer_result == "skipped":
        return " -- "  # correctness-gated skip
    if _is_gracefully_skipped(layer_result):
        return "SKIP"
    return "PASS" if layer_result.passed else "FAIL"


def _failure_note(layer_result) -> str:
    """One-line note explaining a layer's status. Empty if normal pass."""
    if isinstance(layer_result, str) and layer_result == "skipped":
        return "skipped: correctness failed"
    if _is_gracefully_skipped(layer_result):
        marker = layer_result.checks[0].name
        msg = layer_result.checks[0].message
        return f"{marker}: {msg}"
    if layer_result.passed:
        return ""
    failed_names = [c.name for c in layer_result.checks if not c.passed]
    return ", ".join(failed_names)


def main():
    parser = argparse.ArgumentParser(
        description="Score one prediction for one named app against the eval-set's rules.",
    )
    parser.add_argument(
        "--app-name",
        required=True,
        help="App identifier in 'app-NN' format (e.g. 'app-08').",
    )
    parser.add_argument(
        "--prediction",
        required=True,
        type=Path,
        help="Path to the prediction JSON file (the agent's recommendation).",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip the LLM judge even if an API key is set. "
             "Mid and Rich return '(skipped)' markers; Shape and "
             "Correctness still run normally.",
    )
    args = parser.parse_args()

    # ---- map app-name to scenario id ----
    try:
        scenario_id = _app_name_to_scenario_id(args.app_name)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    # ---- load prediction ----
    if not args.prediction.exists():
        print(f"ERROR: prediction file not found: {args.prediction}",
              file=sys.stderr)
        sys.exit(2)
    try:
        prediction = json.loads(args.prediction.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: prediction file is not valid JSON: {e}",
              file=sys.stderr)
        sys.exit(2)
    if not isinstance(prediction, dict):
        print(f"ERROR: prediction must be a JSON object, "
              f"got {type(prediction).__name__}",
              file=sys.stderr)
        sys.exit(2)

    # ---- maybe construct the LLM judge ----
    judge = None
    if not args.no_judge:
        from .judge_client import JudgeClient
        if JudgeClient.is_available():
            try:
                judge = JudgeClient()
            except Exception as e:
                print(f"WARNING: failed to construct LLM judge: {e}. "
                      f"Falling back to deterministic-only scoring.",
                      file=sys.stderr)

    # ---- load scorer ----
    try:
        scorer = Scorer.from_eval_set_dir(
            DEFAULT_EVAL_SET_DIR,
            dataset_examples_dir=DEFAULT_DATASET_EXAMPLES_DIR,
            judge=judge,
        )
    except FileNotFoundError as e:
        print(f"ERROR: cannot load eval-set rules: {e}", file=sys.stderr)
        sys.exit(2)

    # ---- check scenario id is known ----
    if scenario_id not in scorer.scenario_ids:
        known_apps = ", ".join(f"app-{s}" for s in scorer.scenario_ids)
        print(f"ERROR: unknown app {args.app_name!r}. "
              f"Known apps: {known_apps}",
              file=sys.stderr)
        sys.exit(2)

    # ---- score ----
    result = scorer.score_one(scenario_id, prediction)

    # ---- render ----
    print()
    judge_note = "with LLM judge" if judge else "deterministic only"
    print(f"App {args.app_name} score ({judge_note}):")
    layer_order = [
        ("Shape",       result["shape"]),
        ("Correctness", result["correctness"]),
        ("Mid",         result["mid"]),
        ("Rich",        result["rich"]),
    ]
    for label, layer_result in layer_order:
        verdict = _verdict_label(layer_result)
        note = _failure_note(layer_result)
        if note:
            print(f"  {label:<12s} {verdict}   ({note})")
        else:
            print(f"  {label:<12s} {verdict}")
    print()

    # ---- summary line + exit code ----
    shape_passed = result["shape"].passed
    correctness_passed = result["correctness"].passed

    def _actually_failed(r) -> bool:
        """A layer that ran and failed (not a graceful skip)."""
        if isinstance(r, str) and r == "skipped":
            return False  # correctness-gated; already reported below
        if _is_gracefully_skipped(r):
            return False
        return not r.passed

    if not shape_passed:
        print("Shape failed; prediction is not well-formed.")
        sys.exit(1)
    if not correctness_passed:
        print("Correctness gate failed; scoring incomplete.")
        sys.exit(1)
    if _actually_failed(result["mid"]) or _actually_failed(result["rich"]):
        print("Mid or Rich failed; prediction is correct but thin.")
        sys.exit(1)

    if judge:
        print("All layers passed.")
    else:
        print("Shape and Correctness passed. Mid and Rich skipped "
              "(judge unavailable). Set OPENAI_API_KEY or "
              "ANTHROPIC_API_KEY in .env to run the full four-layer "
              "score.")
    sys.exit(0)


if __name__ == "__main__":
    main()
