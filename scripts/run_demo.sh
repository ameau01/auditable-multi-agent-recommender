#!/usr/bin/env bash
# Run the eval-set demo: score app-08's gold answer and print the per-layer verdict.
#
# Usage:
#   scripts/run_demo.sh                   # Shape + Correctness only (no API key)
#   scripts/run_demo.sh --with-judge      # adds Mid + Rich via the LLM judge
#
# Flags:
#   --with-judge        Add Mid + Rich layers (needs OPENAI_API_KEY or ANTHROPIC_API_KEY).
#   -h, --help          Show this help message and exit.
#
# Any flags passed are forwarded to eval-set/demo_scoring.py as-is.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python eval-set/demo_scoring.py "$@"
