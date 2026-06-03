#!/usr/bin/env bash
# Run all integration tests under tests/integration/.
#
# Usage:
#   scripts/run_integration.sh [pytest args]
#
# Flags:
#   -h, --help          Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python -m pytest tests/integration/ "$@"
