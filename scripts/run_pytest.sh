#!/usr/bin/env bash
# Run the full pytest suite (unit + integration).
#
# Auto-skips HF-dependent tests when offline.
#
# Usage:
#   scripts/run_pytest.sh                    # default: full sweep
#   scripts/run_pytest.sh -x                 # stop at first failure
#   scripts/run_pytest.sh -k composite       # run only matching tests
#   scripts/run_pytest.sh tests/unit/        # just unit tests
#
# Flags:
#   -h, --help          Show this help message and exit.
#
# Any pytest flags after the script name are passed through.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python -m pytest -q "$@"
