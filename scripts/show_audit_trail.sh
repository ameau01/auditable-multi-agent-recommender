#!/usr/bin/env bash
# Show the audit trail for one cycle.
#
# Usage:
#   scripts/show_audit_trail.sh [APP] [CYCLE_ID] [--content] [--list] [--json]
#
# Args:
#   APP                 Optional app-NN. Omit for the most recent cycle in any app.
#   CYCLE_ID            Optional cycle_id. With APP, must belong to APP.
#
# Flags:
#   --content           Dump JSON content column instead of per-row summary.
#   --list              Skip the dump and print the cycle catalog instead.
#   --json              Machine-readable output.
#   -h, --help          Show this help message and exit.
#
# Examples:
#   scripts/show_audit_trail.sh app-08
#   scripts/show_audit_trail.sh app-08 <cycle> --content

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

# Detect --list anywhere on the command line and route to the list command.
if [[ " $* " == *" --list "* ]] || [[ "${@: -1}" == "--list" ]]; then
  argv=()
  for a in "$@"; do
    [[ "$a" == "--list" ]] && continue
    argv+=("$a")
  done
  exec uv run python -m src.audit.inspect list "${argv[@]}"
fi

# Otherwise: pass through to `show` with positional [APP] [CYCLE] and flags.
exec uv run python -m src.audit.inspect show "$@"
