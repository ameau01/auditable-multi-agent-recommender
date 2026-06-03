#!/usr/bin/env bash
# Run gold-answer validation: every gold through the four-layer scorer.
# Headline benchmark-integrity check.
#
# Usage:
#   scripts/run_golden.sh [pytest args]
#
# Flags:
#   -h, --help          Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python -m pytest tests/integration/test_golden_answers.py "$@"
