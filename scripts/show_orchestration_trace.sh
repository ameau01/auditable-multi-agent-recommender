#!/usr/bin/env bash
# Show a structured trace of one cycle.
#
# Usage:
#   scripts/show_orchestration_trace.sh [APP] [CYCLE_ID] [--type TYPE[,TYPE]] [--content] [--list] [--json]
#
# Args:
#   APP                  Optional app-NN. Omit for the most recent cycle in any app.
#   CYCLE_ID             Optional cycle_id. With APP, must belong to APP.
#
# Flags:
#   --type TYPE[,TYPE]   Comma-separated subset of {decisions, evidence}.
#                        Default: decisions,evidence (both).
#   --content            Dump JSON content column instead of per-row summary.
#   --list               Skip the trace and print the cycle catalog instead.
#   --json               Machine-readable output.
#   -h, --help           Show this help message and exit.
#
# Examples:
#   scripts/show_orchestration_trace.sh app-08 --type decisions
#   scripts/show_orchestration_trace.sh app-08 --type decisions,evidence
#   scripts/show_orchestration_trace.sh app-08 <cycle> --content

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

if [[ " $* " == *" --list "* ]] || [[ "${@: -1}" == "--list" ]]; then
  argv=()
  for a in "$@"; do
    [[ "$a" == "--list" ]] && continue
    argv+=("$a")
  done
  exec uv run python -m src.audit.inspect list "${argv[@]}"
fi

exec uv run python -m src.audit.inspect trace "$@"
