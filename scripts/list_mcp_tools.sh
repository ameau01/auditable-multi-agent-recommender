#!/usr/bin/env bash
# Print the catalog of tools the MCP server exposes.
#
# Usage:
#   scripts/list_mcp_tools.sh                       # human-readable table
#   scripts/list_mcp_tools.sh --json                # machine-readable JSON
#   scripts/list_mcp_tools.sh --schema TOOL_NAME    # full input schema for one tool
#
# Flags:
#   --json                Machine-readable JSON output.
#   --schema TOOL_NAME    Print the JSON-Schema for one tool's args.
#   -h, --help            Show this help message and exit.

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

set -e
cd "$(dirname "$0")/.."

uv run python tests/list_mcp_tools.py "$@"
