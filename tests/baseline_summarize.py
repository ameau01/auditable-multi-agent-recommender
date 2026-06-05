"""Summarize a baseline-runs/<dir>/ folder into a polished single-file summary.

Reads:
  - <dir>/app-NN/score.txt         — the eval.py four-layer status (Stage 2)
  - <dir>/app-NN/usage.json        — per-app generation elapsed (Stage 1)
  - <dir>/00_generation.log        — config + per-app generation PASS/FAIL

Produces (to stdout, or to a file with --output):
  - Configuration header (model, provider, time, out_dir)
  - Stage 1: generation totals
  - Stage 2: per-layer X/N table with (cond.) denominators for Mid + Rich
  - Per-app verdict table

Usage:
  python tests/baseline_summarize.py baseline-runs/haiku-single-shot
  python tests/baseline_summarize.py baseline-runs/haiku-single-shot \\
      --output baseline-runs/haiku-single-shot/00_summary.txt
  python tests/baseline_summarize.py baseline-runs/*-single-shot

Safe to run while the baseline is still in flight — uncounted apps just
don't appear yet. Re-run after completion to get the final table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

LAYERS = ("Shape", "Correctness", "Mid", "Rich")

# Matches:
#   "  Shape        PASS"                          -> ("Shape", "PASS",   "")
#   "  Rich         SKIP   (short_circuit: ...)"   -> ("Rich",  "SKIP",   "(short_circuit: ...)")
#   "  Rich         SKIP   (skipped: judge ...)"   -> ("Rich",  "SKIP",   "(skipped: judge ...)")
#   "  Mid           --    (skipped: correct...)"  -> ("Mid",   "SKIP",   "(skipped: correctness ...)")  (normalized)
#
# We capture the parenthetical so the post-processor can distinguish a
# correct-verdict SKIP (short_circuit OR judge-unavailable on a no-action
# finding, both of which eval.py reports as "All layers passed.") from a
# gated-out SKIP (Correctness failed → Mid/Rich didn't run). Strict-PASS
# counting treats both as not-PASS; effective-PASS counting treats only
# the gated kind as not-PASS.
#
# Note: trailing assertion is `(\s|$)` not `\b`. The `\b` form fails on
# `--` because both `-` and the following space are non-word characters,
# so there's no word boundary between them. `(\s|$)` works for all four
# verdict shapes uniformly.
_LAYER_LINE_RE = re.compile(
    r"^\s*(Shape|Correctness|Mid|Rich)\s+(PASS|FAIL|SKIP|--)\s*(\(.*?\))?\s*$"
)

# A SKIP whose parenthetical starts with "short_circuit" or "skipped: judge"
# is a CORRECT verdict — eval.py reports "All layers passed." for the file.
# A SKIP that says "skipped: correctness failed" is a gated-out skip and
# does NOT count as a correct verdict.
_CORRECT_SKIP_RE = re.compile(
    r"\((short_circuit|skipped:\s*judge)\b",
    re.IGNORECASE,
)


def parse_score_file(path: Path) -> dict[str, str]:
    """Return {layer: verdict} for the four layers found in one score.txt.

    Verdict values:
      PASS         — explicit pass
      FAIL         — explicit fail
      SKIP_OK      — SKIP that's a correct verdict (short_circuit or
                     "judge unavailable" on a no-action finding).
                     eval.py reports "All layers passed." for the file.
      SKIP_GATED   — SKIP because Correctness failed (gated out).
                     eval.py reports "Correctness gate failed".
      MISSING      — layer line not found (eval.py crashed early).

    The two SKIP flavors are distinguished by the parenthetical: a
    short_circuit / judge-unavailable parenthetical means correct
    verdict; "skipped: correctness failed" means gated.

    `--` (eval.py's gated-out marker) is normalized to SKIP_GATED.
    """
    verdicts: dict[str, str] = {layer: "MISSING" for layer in LAYERS}
    try:
        text = path.read_text()
    except OSError:
        return verdicts
    for line in text.splitlines():
        m = _LAYER_LINE_RE.match(line)
        if not m:
            continue
        layer = m.group(1)
        raw = m.group(2)
        paren = m.group(3) or ""
        if raw in ("PASS", "FAIL"):
            verdicts[layer] = raw
        elif raw == "--":
            # eval.py's gated-out marker — always Correctness-gated.
            verdicts[layer] = "SKIP_GATED"
        else:  # raw == "SKIP"
            if _CORRECT_SKIP_RE.search(paren):
                verdicts[layer] = "SKIP_OK"
            else:
                verdicts[layer] = "SKIP_GATED"
    return verdicts


# Counts as a correct verdict for the layer.
EFFECTIVE_PASS = {"PASS", "SKIP_OK"}


def parse_usage_file(path: Path) -> float | None:
    """Pull elapsed_s out of a per-app usage.json; None on any failure."""
    try:
        data = json.loads(path.read_text())
        return float(data.get("elapsed_s", 0)) or None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def parse_gen_log(path: Path) -> dict[str, str]:
    """Pull header fields out of 00_generation.log into a flat dict.

    Returns keys like: started_at, model, provider, out_dir. Empty dict
    if the file is missing or unparseable.
    """
    header: dict[str, str] = {}
    if not path.exists():
        return header
    try:
        text = path.read_text()
    except OSError:
        return header
    for line in text.splitlines():
        if ":" in line and not line.startswith("  ") and not line.startswith("-"):
            key, _, value = line.partition(":")
            key = key.strip().lstrip("#").strip()
            value = value.strip()
            if key and value and key in {
                "started_at", "model", "provider", "out_dir", "apps",
            }:
                header[key] = value
    return header


# ============================================================
# Rendering
# ============================================================
def render_summary(run_dir: Path) -> str:
    """Produce the polished single-file summary for one baseline run.

    Supports both layouts:
      - single-shot baseline: <run_dir>/app-NN/score.txt
      - orchestrated integration-test: <run_dir>/step2_scoring/app-NN.score
    """
    score_files = sorted(run_dir.glob("app-*/score.txt"))
    if not score_files:
        score_files = sorted(run_dir.glob("step2_scoring/app-*.score"))
    gen_log = parse_gen_log(run_dir / "00_generation.log")

    # If no scoring has happened yet, give the user a useful message
    # rather than a blank table.
    if not score_files:
        return (
            f"# Baseline summary — {run_dir.name}\n\n"
            f"(No app-*/score.txt found under {run_dir}. "
            f"Has Stage 2 (scoring) run yet?)\n"
        )

    per_layer: dict[str, Counter[str]] = {
        layer: Counter() for layer in LAYERS
    }
    apps_seen: list[tuple[str, dict[str, str], float | None]] = []

    for sf in score_files:
        # Two layouts:
        #   single-shot: <run>/app-NN/score.txt  (app from parent dir)
        #   orchestrated: <run>/step2_scoring/app-NN.score (app from stem)
        if sf.name == "score.txt":
            app = sf.parent.name
            usage_path = sf.parent / "usage.json"
        else:
            app = sf.stem  # 'app-NN'
            usage_path = None  # orchestrated runs don't write usage.json
        verdicts = parse_score_file(sf)
        elapsed = parse_usage_file(usage_path) if usage_path else None
        apps_seen.append((app, verdicts, elapsed))
        for layer in LAYERS:
            per_layer[layer][verdicts[layer]] += 1

    n = len(apps_seen)
    all_four_effective = sum(
        1
        for _, v, _ in apps_seen
        if all(v[layer] in EFFECTIVE_PASS for layer in LAYERS)
    )
    total_elapsed = sum(e for _, _, e in apps_seen if e is not None)
    avg_elapsed = total_elapsed / n if n else 0.0

    # --------- header ---------
    lines: list[str] = []
    lines.append(f"# Baseline summary — {run_dir.name}")
    lines.append("=" * 64)
    lines.append("")
    lines.append("Configuration")
    lines.append("-" * 13)
    if gen_log:
        for key in ("model", "provider", "started_at", "out_dir"):
            if key in gen_log:
                lines.append(f"  {key:<11}: {gen_log[key]}")
    lines.append(f"  {'apps':<11}: {n}")
    lines.append("")

    # --------- Stage 1 ---------
    lines.append("Stage 1 — generation")
    lines.append("-" * 20)
    lines.append(f"  scored apps   : {n} / {n}")
    lines.append(
        f"  total elapsed : {total_elapsed:.1f}s  "
        f"(avg {avg_elapsed:.1f}s / app)"
    )
    lines.append("")

    # --------- Stage 2 ---------
    # Headline counts:
    #   pass = correct verdict (explicit PASS, OR correct SKIP on no-action
    #          scenarios where there's nothing to judge — matches gold)
    #   fail = wrong verdict (explicit FAIL)
    #   n/a  = layer didn't run (Correctness gate failed, or scorer crashed)
    lines.append("Stage 2 — scoring (four layers)")
    lines.append("-" * 31)
    lines.append("")
    lines.append(
        f"  {'layer':<12} {'pass':>5} {'fail':>5} {'n/a':>5} {'total':>6}"
    )
    lines.append(
        f"  {'-' * 12} {'-' * 5} {'-' * 5} {'-' * 5} {'-' * 6}"
    )
    for layer in LAYERS:
        c = per_layer[layer]
        pass_count = c["PASS"] + c["SKIP_OK"]
        fail_count = c["FAIL"]
        na_count = c["SKIP_GATED"] + c["MISSING"]
        lines.append(
            f"  {layer:<12} {pass_count:>5} {fail_count:>5} "
            f"{na_count:>5} {n:>6}"
        )
    lines.append("")
    lines.append(f"  all-four-PASS apps : {all_four_effective} / {n}")
    lines.append("")
    lines.append("  pass = correct verdict (explicit PASS, or correct short-circuit on")
    lines.append("         no-action scenarios where there's nothing to judge)")
    lines.append("  fail = wrong verdict (explicit FAIL)")
    lines.append("  n/a  = layer didn't run (Correctness gate failed, or scorer crashed)")
    lines.append("")

    # --------- per-app ---------
    # Normalize per-app verdicts to the same 3-class headline view.
    def _normalize(verdict: str) -> str:
        if verdict in EFFECTIVE_PASS:  # PASS or SKIP_OK
            return "PASS"
        if verdict == "FAIL":
            return "FAIL"
        return "n/a"  # SKIP_GATED or MISSING — layer didn't run

    lines.append("Per-app verdicts")
    lines.append("-" * 16)
    lines.append("")
    lines.append(
        f"  {'app':<8}  {'Shape':<5}  {'Correctness':<11}  "
        f"{'Mid':<5}  {'Rich':<5}   {'elapsed':>8}"
    )
    lines.append(
        f"  {'-' * 8}  {'-' * 5}  {'-' * 11}  {'-' * 5}  {'-' * 5}   "
        f"{'-' * 8}"
    )
    for app, v, elapsed in apps_seen:
        elapsed_str = f"{elapsed:.2f}s" if elapsed is not None else "-"
        lines.append(
            f"  {app:<8}  {_normalize(v['Shape']):<5}  "
            f"{_normalize(v['Correctness']):<11}  "
            f"{_normalize(v['Mid']):<5}  {_normalize(v['Rich']):<5}   "
            f"{elapsed_str:>8}"
        )
    lines.append("")
    lines.append("=" * 64)

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize a baseline-runs/<dir>/ folder produced by "
            "scripts/baseline_single_shot.sh into a polished single-file "
            "summary suitable for the README baseline table."
        ),
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        help="One or more baseline-runs/<dir>/ paths.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Write the summary to this file instead of stdout. "
            "Only meaningful with a single <run-dir>. With multiple "
            "<run-dir>s the summaries are concatenated to stdout."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.output and len(args.run_dirs) > 1:
        print(
            "ERROR: --output only valid with a single <run-dir>",
            file=sys.stderr,
        )
        return 2

    summaries = []
    for arg in args.run_dirs:
        run_dir = Path(arg).resolve()
        if not run_dir.exists():
            print(f"  skip: {run_dir} does not exist", file=sys.stderr)
            continue
        summaries.append(render_summary(run_dir))

    output_text = "\n\n".join(summaries) + "\n"
    if args.output:
        args.output.write_text(output_text)
        print(f"Wrote summary to {args.output}")
    else:
        sys.stdout.write(output_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
