#!/usr/bin/env bash
# Run the audit-store end-to-end smoke (temp DB; write -> query -> compose -> render).
#
# Usage:
#   scripts/run_audit_smoke.sh [pytest args]
#
# Flags:
#   -h, --help          Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python tests/audit_smoke.py "$@"
