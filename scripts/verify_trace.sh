#!/usr/bin/env bash
# Walk every sample-run audit trail backward and confirm every parent
# reference resolves. Exits 0 on clean walk; non-zero on any dangling
# pointer.
#
# Usage:
#   scripts/verify_trace.sh
#
# Flags:
#   -h, --help          Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python tests/verify_trace.py "$@"
