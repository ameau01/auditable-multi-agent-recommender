#!/usr/bin/env bash
# Run the unit tests for the audit store layer.
#
# Usage:
#   scripts/run_audit_tests.sh                    # default verbose
#   scripts/run_audit_tests.sh -x                 # stop at first failure
#   scripts/run_audit_tests.sh -k chain           # filter to matching tests
#
# Flags:
#   -h, --help          Show this help message and exit.
#
# Any pytest flags after the script name are passed through.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python -m pytest tests/unit/audit/ -v "$@"
