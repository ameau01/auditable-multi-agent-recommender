"""One-shot helper: capture real LLM-judge verdicts as committed fixtures.

Notebook 02 (`02_Evaluation_and_Results.ipynb`) wants to display real Mid + Rich
verdicts so a reader sees the project's central value proposition — orchestrated
agents earning richness scores a single-shot baseline can't — without needing
to set up an API key. This script does the one-time capture: it scores the gold
answer against itself (gold-vs-gold) for three scenarios with the real Anthropic
judge enabled, then writes the verdict + rationale + provider + model + timestamp
to scenario_NN_judge.json files in this folder.

Gold-vs-gold is the right thing to score here because:
  1. It's the strongest signal that the eval pipeline works end-to-end.
  2. The rationale text the judge produces — naming specific phrases in the
     gold's specific_change and explaining why they earn the Rich threshold —
     is exactly what a hiring manager wants to see, and what no synthetic
     stand-in could honestly produce.

Cost: ~$0.01 total (one Haiku judge call per scenario × 3 scenarios).

Usage:
    cd agent-orchestration
    uv run python tests/integration/notebook_fixtures/generate_judge_fixtures.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


SCENARIOS = ["02", "07", "08"]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = Path(__file__).resolve().parent
EVAL_SET_DIR = PROJECT_ROOT / "eval-set"
DATASET_EXAMPLES_DIR = PROJECT_ROOT / "dataset-examples"


def main() -> int:
    # Make project root importable.
    sys.path.insert(0, str(PROJECT_ROOT))

    # Lazy import — these pull anthropic SDK + load .env.
    from src.common.init import ensure_env_loaded
    from src.evaluator.evaluator import Scorer
    from src.evaluator.judge_client import JudgeClient

    ensure_env_loaded()

    if not JudgeClient.is_available():
        print(
            "ERROR: ANTHROPIC_API_KEY not set in .env or environment.\n"
            "       This generator needs one real judge call per scenario\n"
            "       (3 calls total, ~$0.01 with the default Haiku judge).\n"
            "       Add ANTHROPIC_API_KEY=sk-ant-... to .env, then re-run.",
            file=sys.stderr,
        )
        return 2

    judge = JudgeClient()
    scorer = Scorer.from_eval_set_dir(
        EVAL_SET_DIR,
        dataset_examples_dir=DATASET_EXAMPLES_DIR,
        judge=judge,
    )

    print(f"Judge provider : {judge.provider}")
    print(f"Judge model    : {judge.model}")
    print(f"Output folder  : {FIXTURE_DIR.relative_to(PROJECT_ROOT)}")
    print()

    n_ok = 0
    n_err = 0
    for sid in SCENARIOS:
        gold_path = EVAL_SET_DIR / "expectations" / sid / "raw_recommendation.json"
        if not gold_path.exists():
            print(f"  [{sid}] SKIP — gold answer not found at {gold_path.relative_to(PROJECT_ROOT)}")
            n_err += 1
            continue

        gold = json.loads(gold_path.read_text())

        print(f"  [{sid}] scoring gold-vs-gold with judge... ", end="", flush=True)
        t0 = time.time()
        try:
            result = scorer.score_one(sid, dict(gold))
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}")
            n_err += 1
            continue
        elapsed = time.time() - t0

        # Extract the JudgeResult for Mid and Rich from the scoring result.
        # Both layers go through the same judge call internally; we want the
        # judge's per-layer score + rationale.
        mid_layer = result.get("mid")
        rich_layer = result.get("rich")

        fixture = {
            "scenario_id": sid,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "judge_provider": judge.provider,
            "judge_model": judge.model,
            "elapsed_seconds": round(elapsed, 2),
            "shape_passed": _layer_passed(result.get("shape")),
            "correctness_passed": _layer_passed(result.get("correctness")),
            "mid": _layer_to_dict(mid_layer),
            "rich": _layer_to_dict(rich_layer),
        }

        out_path = FIXTURE_DIR / f"scenario_{sid}_judge.json"
        out_path.write_text(json.dumps(fixture, indent=2, default=str))
        print(f"OK ({elapsed:.1f}s) -> {out_path.name}")
        n_ok += 1

    print()
    print(f"Done. {n_ok} fixture(s) written, {n_err} error(s).")
    return 0 if n_err == 0 else 1


def _layer_passed(layer) -> bool | None:
    """True if a TierResult passed, None if it was skipped or absent."""
    if layer is None or isinstance(layer, str):
        return None
    return bool(getattr(layer, "passed", False))


def _layer_to_dict(layer) -> dict:
    """Convert a TierResult to a plain dict for JSON serialization."""
    if layer is None:
        return {"status": "missing"}
    if isinstance(layer, str):
        return {"status": layer}  # e.g. "skipped"
    out: dict = {
        "status": "scored",
        "passed": bool(getattr(layer, "passed", False)),
        "checks": [],
    }
    for check in getattr(layer, "checks", []) or []:
        # The dataclass CheckResult (src/evaluator/types.py) has these fields:
        # name, passed, message (str), detail (dict with score+rationale).
        out["checks"].append({
            "name": getattr(check, "name", None),
            "passed": getattr(check, "passed", None),
            "message": getattr(check, "message", ""),
            "detail": dict(getattr(check, "detail", {}) or {}),
        })
    return out


if __name__ == "__main__":
    raise SystemExit(main())
