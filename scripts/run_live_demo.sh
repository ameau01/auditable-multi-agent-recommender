#!/usr/bin/env bash
# Live-LLM demo — run the orchestrated agent pipeline against one app,
# render the report + trace. Needs an API key (ANTHROPIC_API_KEY or
# OPENAI_API_KEY) in the environment.
#
# This is what `docker compose up live-llm` runs inside the container.
# Outputs land in --out-dir (default: ./demo-output/, which the docker
# compose service mounts as /out so the host can open the files after).
#
# Three stages:
#   1. Run the agent system end-to-end on $APP via scripts/run_agents.sh.
#      The cycle's audit records land in $AUDIT_DB_PATH.
#   2. Render report.md from the resulting cycle (no MOCK MODE banner;
#      this is a real run).
#   3. Render trace.json + trace.md from the same cycle.
#
# Usage:
#   scripts/run_live_demo.sh                     # default: app-08, ./demo-output/
#   scripts/run_live_demo.sh --app app-12        # different scenario
#   scripts/run_live_demo.sh --out-dir /out      # for the docker container
#
# Flags:
#   --app APP-NN    Application id to review. Default: app-08.
#   --out-dir DIR   Where to write report.md / trace.json / trace.md.
#                   Default: ./demo-output/
#   -h, --help      Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

APP="${APP:-app-08}"   # honors APP env var (lets docker compose override)
OUT_DIR="./demo-output"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)     APP="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT_DIR"

# Fresh audit DB just for this demo run. The render scripts read from
# AUDIT_DB_PATH, so setting it here means they automatically find the
# cycle the agents just produced.
DEMO_DB="$OUT_DIR/.live_audit.db"
rm -f "$DEMO_DB"
export AUDIT_DB_PATH="$DEMO_DB"

echo "================================================================"
echo " Live-LLM demo"
echo "   app       : $APP"
echo "   out_dir   : $OUT_DIR"
echo "   audit_db  : $DEMO_DB"
echo "================================================================"

# ----------------------------------------------------------------------
# Stage 1: run agents (this is the live LLM call)
# ----------------------------------------------------------------------
echo
echo "--- Stage 1: run the agent pipeline (live LLM) ---"
echo "    Heads-up: this is the slow stage. One cycle = System Mapper +"
echo "    Compute / Data-Layer / Network specialists + Cross-Tier Evaluator,"
echo "    each making real LLM calls. Typical wall time:"
echo "      Haiku  specialists + Haiku  evaluator : ~3-5 min"
echo "      Sonnet specialists + Sonnet evaluator : ~5-8 min"
echo "      Opus   specialists + Opus   evaluator : ~6-10 min"
echo "    The progress line below stays put until the cycle finishes — that's"
echo "    expected, not a hang. Ctrl-C cancels."
echo
./scripts/run_agents.sh "$APP"

# ----------------------------------------------------------------------
# Stage 2: render report (NO mock-mode banner — this is a real run)
# ----------------------------------------------------------------------
echo
echo "--- Stage 2: render report.md ---"
./scripts/render_recommendation.sh "$APP" --out "$OUT_DIR/report.md"

# ----------------------------------------------------------------------
# Stage 3: render trace.json + trace.md
# ----------------------------------------------------------------------
echo
echo "--- Stage 3: render trace.json + trace.md ---"
./scripts/render_evidence_trace.sh "$APP" --format json --out "$OUT_DIR/trace.json"
./scripts/render_evidence_trace.sh "$APP" --format markdown --out "$OUT_DIR/trace.md"

echo
echo "================================================================"
echo " Done."
echo "   Open these to read the demo output:"
echo "     $OUT_DIR/report.md     (live LLM recommendation)"
echo "     $OUT_DIR/trace.md      (human-readable audit trail)"
echo "     $OUT_DIR/trace.json    (machine-readable audit trail)"
echo "================================================================"
