#!/usr/bin/env bash
# Run the MCP server wire-layer integration test.
#
# Auto-skips when Hugging Face is unreachable from this network.
#
# Usage:
#   scripts/run_mcp_server_test.sh           # default verbose run
#   scripts/run_mcp_server_test.sh -x        # stop at first failure
#   scripts/run_mcp_server_test.sh -k list   # run only test_list_scenarios*
#
# Flags:
#   -h, --help          Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python -m pytest tests/integration/test_mcp_server.py -v "$@"
