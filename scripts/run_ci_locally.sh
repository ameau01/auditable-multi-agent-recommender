#!/usr/bin/env bash
# Run the same checks GitHub Actions runs, locally and in the same order.
# Pre-push gate: if this passes, the workflow_dispatch CI run will pass.
#
# Steps (mirrors .github/workflows/ci.yml):
#   1. ruff check     (linting)
#   2. mypy src       (type checking)
#   3. pytest -q      (full test suite)
#
# Stops at the first failure (set -e) so you fix one thing at a time.
#
# Usage:
#   scripts/run_ci_locally.sh
#
# Flags:
#   -h, --help          Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

banner() {
  echo
  echo "============================================================"
  echo "  $1"
  echo "============================================================"
}

banner "1/3  Linting (ruff check .)"
uv run ruff check .

banner "2/3  Type checking (mypy src)"
uv run mypy src

banner "3/3  Tests (pytest -q)"
uv run python -m pytest -q

banner "All CI checks passed locally. Safe to push."
