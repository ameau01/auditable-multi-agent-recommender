#!/usr/bin/env bash
# Run the unit tests for the audit store layer.
#
# Covers:
#   - tests/unit/audit/test_store.py     — lifecycle + append-only + FK + uniqueness
#   - tests/unit/audit/test_queries.py   — CTE walks, json_each, grouping
#   - tests/unit/audit/test_composer.py  — compose_from_cycle + renderer integration
#
# Usage:
#   scripts/run_audit_tests.sh                    # default verbose
#   scripts/run_audit_tests.sh -x                 # stop at first failure
#   scripts/run_audit_tests.sh -k chain           # filter to matching tests

set -e
cd "$(dirname "$0")/.."

uv run python -m pytest tests/unit/audit/ -v "$@"
