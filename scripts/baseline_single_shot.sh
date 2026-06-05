#!/usr/bin/env bash
# Single-shot LLM baseline + scoring.
#
# Two stages plus a summary step:
#   1. Generation: tests/baseline_single_shot.py produces a
#      Recommendation per app under <out-dir>/app-NN/recommendation.json.
#      Verbose log at <out-dir>/00_generation.log.
#   2. Scoring: feed each recommendation through the same four-layer
#      evaluator (Shape / Correctness / Mid / Rich) the orchestrated
#      system uses, via `python -m src.evaluator.eval --app-name
#      app-NN --prediction <path>`. Per-app score at
#      <out-dir>/app-NN/score.txt; per-app one-liners at
#      <out-dir>/00_scoring.log.
#   3. Summary: scripts/baseline_summarize.py reads the above and
#      writes a polished single-file summary at
#      <out-dir>/00_summary.txt. This is the file you open to read
#      the X/18 per-layer table.
#
# Output folder is separate from any orchestrated-run folder so the
# audit DB and integration-test artifacts are untouched.
#
# Usage:
#   scripts/baseline_single_shot.sh --model haiku
#   scripts/baseline_single_shot.sh --model sonnet --apps app-08
#   scripts/baseline_single_shot.sh --model opus --no-judge
#
# Flags:
#   --model {haiku|sonnet|opus}   Required.
#   --provider {anthropic|openai} Default: anthropic.
#   --apps app-NN[,app-NN,...]    Default: all 18.
#   --out-dir DIR                 Default: baseline-runs/<model>-<UTC ts>.
#   --skip-generation             Re-score an existing <out-dir>.
#   --skip-scoring                Generate only; skip Stage 2 + summary.
#   --no-judge                    Stage 2: skip the LLM judge for Mid/Rich.
#   -h, --help                    Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

MODEL=""
PROVIDER="anthropic"
APPS=""
OUT_DIR=""
SKIP_GEN=""
SKIP_SCORE=""
NO_JUDGE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)          MODEL="$2"; shift 2 ;;
    --provider)       PROVIDER="$2"; shift 2 ;;
    --apps)           APPS="$2"; shift 2 ;;
    --out-dir)        OUT_DIR="$2"; shift 2 ;;
    --skip-generation) SKIP_GEN="1"; shift ;;
    --skip-scoring)   SKIP_SCORE="1"; shift ;;
    --no-judge)       NO_JUDGE="--no-judge"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$MODEL" ]]; then
  echo "ERROR: --model required (haiku|sonnet|opus)" >&2
  exit 2
fi

# Resolve OUT_DIR if not supplied. Use the same template the Python
# script picks so re-running with the same model+ts works.
if [[ -z "$OUT_DIR" ]]; then
  TS="$(date -u +%Y%m%d_%H%M%S)"
  OUT_DIR="baseline-runs/${MODEL}-${TS}"
fi

mkdir -p "$OUT_DIR"
echo "================================================================"
echo " Single-shot baseline run"
echo "   model     : $MODEL"
echo "   provider  : $PROVIDER"
echo "   apps      : ${APPS:-(all 18)}"
echo "   out_dir   : $OUT_DIR"
echo "================================================================"

# ----------------------------------------------------------------------
# Stage 1: generation
# ----------------------------------------------------------------------
if [[ -z "$SKIP_GEN" ]]; then
  echo
  echo "--- Stage 1: generate recommendations ---"
  GEN_ARGS=(--model "$MODEL" --provider "$PROVIDER" --out-dir "$OUT_DIR")
  if [[ -n "$APPS" ]]; then
    GEN_ARGS+=(--apps "$APPS")
  fi
  # Allow per-app Stage 1 failures (Pydantic-invalid LLM output for one
  # app shouldn't kill Stage 2/3 for the other 17). Stage 2 already
  # handles missing recommendation.json files per-app. The final exit
  # code below still reflects the run's overall status.
  set +e
  uv run python tests/baseline_single_shot.py "${GEN_ARGS[@]}"
  STAGE1_RC=$?
  set -e
  if [[ $STAGE1_RC -ne 0 ]]; then
    echo
    echo "(Stage 1 reported rc=$STAGE1_RC — one or more apps failed"
    echo " generation; continuing to Stage 2 with the apps that did succeed.)"
  fi
else
  echo "(skipping generation; using existing $OUT_DIR)"
fi

# ----------------------------------------------------------------------
# Stage 2: scoring
# ----------------------------------------------------------------------
if [[ -n "$SKIP_SCORE" ]]; then
  echo
  echo "(skipping scoring + summary)"
  exit 0
fi

echo
echo "--- Stage 2: score against eval-set (four layers) ---"

SCORING_LOG="$OUT_DIR/00_scoring.log"
{
  echo "# Per-app scoring (Shape / Correctness / Mid / Rich)"
  echo "model: $MODEL"
  echo "out_dir: $OUT_DIR"
  echo
} > "$SCORING_LOG"

n_score_pass=0
n_score_fail=0

# Iterate sorted app dirs so output is deterministic.
shopt -s nullglob
for app_dir in $(ls -d "$OUT_DIR"/app-* 2>/dev/null | sort); do
  app="$(basename "$app_dir")"
  pred="$app_dir/recommendation.json"
  if [[ ! -f "$pred" ]]; then
    echo "  - $app: SKIP (no recommendation.json)" | tee -a "$SCORING_LOG"
    continue
  fi
  score_out="$app_dir/score.txt"
  set +e
  uv run python -m src.evaluator.eval \
      --app-name "$app" --prediction "$pred" $NO_JUDGE \
      > "$score_out" 2>&1
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    n_score_pass=$((n_score_pass + 1))
    verdict="PASS"
  else
    n_score_fail=$((n_score_fail + 1))
    verdict="FAIL"
  fi
  # Pull all four layer status lines so the per-app log line is useful.
  layers_line="$(grep -E '^\s+(Shape|Correctness|Mid|Rich)' "$score_out" \
                 | tr '\n' ' | ' | sed 's/  */ /g')"
  echo "  - $app: $verdict  ${layers_line:-(see $score_out)}" \
    | tee -a "$SCORING_LOG"
done

echo | tee -a "$SCORING_LOG"
echo "totals: eval_pass=$n_score_pass  eval_fail=$n_score_fail  (these are eval.py exit codes; see 00_summary.txt for per-layer X/N)" \
  | tee -a "$SCORING_LOG"

# ----------------------------------------------------------------------
# Stage 3: polished summary
# ----------------------------------------------------------------------
echo
echo "--- Stage 3: render polished summary ---"
uv run python scripts/baseline_summarize.py "$OUT_DIR" \
    --output "$OUT_DIR/00_summary.txt"
echo

echo "================================================================"
echo " Done."
echo "   Open this for the per-layer table : $OUT_DIR/00_summary.txt"
echo "   Per-app raw scores                : $OUT_DIR/app-*/score.txt"
echo "   Per-app one-liner scoring log     : $OUT_DIR/00_scoring.log"
echo "   Generation execution log          : $OUT_DIR/00_generation.log"
echo "================================================================"

# Exit non-zero only if scoring failed at the eval.py exit-code level
# for one or more apps. Per-layer pass/fail is in 00_summary.txt.
if [[ $n_score_fail -gt 0 ]]; then
  exit 1
fi
