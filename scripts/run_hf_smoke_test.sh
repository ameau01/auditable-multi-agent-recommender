#!/usr/bin/env bash
# Download the published dataset from Hugging Face and print basic facts.
#
# First run downloads ~12 MB; subsequent runs hit the local cache. To
# force a fresh download: scripts/clean.sh --hf
#
# Usage:
#   scripts/run_hf_smoke_test.sh
#
# Flags:
#   -h, --help          Show this help message and exit.
#
# Exits 0 on success, non-zero if the download or parse fails.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python -m src.data_loader
