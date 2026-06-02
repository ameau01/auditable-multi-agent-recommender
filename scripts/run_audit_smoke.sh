#!/usr/bin/env bash
# Run the audit-store end-to-end smoke. Creates a temp SQLite DB, runs a
# synthetic cycle through write -> query -> compose -> render, prints a
# pass/fail summary.
#
# Useful as a manual sanity check after touching anything in src/audit/
# or src/models/audit.py.
#
# Usage:
#   scripts/run_audit_smoke.sh

set -e
cd "$(dirname "$0")/.."

uv run python tests/audit_smoke.py "$@"
