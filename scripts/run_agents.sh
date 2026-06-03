#!/usr/bin/env bash
# Run one cycle of the agent system on an app-name.
#
# Usage:
#   scripts/run_agents.sh <app-NN> [--trigger manual|scheduled|test]
#
# Args:
#   app-NN              The application to review (e.g. app-08).
#
# Flags:
#   --trigger TYPE      Trigger label recorded on the cycle (default: manual).
#   -h, --help          Show this help message and exit.
#
# Example:
#   scripts/run_agents.sh app-08
#   scripts/run_agents.sh app-08 --trigger scheduled

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

APP_NAME="${1:-}"
shift || true

if [[ -z "$APP_NAME" ]]; then
  echo "Usage: scripts/run_agents.sh <app-NN> [--trigger manual|scheduled|test]" >&2
  echo "  e.g.  scripts/run_agents.sh app-08" >&2
  exit 2
fi

TRIGGER_TYPE="manual"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --trigger)
      TRIGGER_TYPE="$2"
      shift 2
      ;;
    *)
      echo "Unknown flag: $1" >&2
      exit 2
      ;;
  esac
done

# Run the agent cycle via a one-liner Python invocation. The runner lives at
# src/agents/runner.py; uv ensures the venv has every dependency.
uv run python - <<PY
from src.agents.runner import run_cycle, langsmith_enabled

cycle_id = run_cycle("$APP_NAME", trigger_type="$TRIGGER_TYPE")
print(f"cycle_id = {cycle_id}")
print(f"langsmith_enabled = {langsmith_enabled()}")
print()
print("Next steps:")
print(f"  scripts/show_audit_trail.sh $APP_NAME")
print(f"  scripts/show_orchestration_trace.sh $APP_NAME --type decisions")
print(f"  scripts/show_orchestration_trace.sh $APP_NAME --type evidence")
print(f"  scripts/show_orchestration_trace.sh $APP_NAME {cycle_id} --type decisions,evidence   # pin this exact cycle")
PY
