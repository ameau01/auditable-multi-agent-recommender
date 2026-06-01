"""Stdio entry point for the MCP server.

Usage:
    python -m src.mcp_server

Or via the wrapper:
    scripts/run_mcp_server.sh

For Claude Desktop integration see docs/mcp-server.md.
"""

from .server import mcp


if __name__ == "__main__":
    mcp.run()
