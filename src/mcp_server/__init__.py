"""MCP server: read-only telemetry contract over the published dataset.

Two consumers share one tool surface:
  - The project's own Tier Specialist agents (called via the Action Harness,
    which scopes each specialist to its allowed tools per `scope.py`).
  - External MCP clients (Claude Desktop, Cursor, etc.) browsing the dataset
    interactively. External clients see the full tool surface; per-tier
    scope is a project-side discipline, not a wire-protocol restriction.

Entry point:
    python -m src.mcp_server      # stdio MCP server

See `docs/mcp-server.md` for the tool contract and `scope.py` for the
per-specialist allow-list.
"""
