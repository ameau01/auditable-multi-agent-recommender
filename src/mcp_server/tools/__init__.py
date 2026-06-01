"""MCP tool implementations, one module per family.

Each module:
  - imports the shared `mcp` instance from `src.mcp_server.server`
  - declares its tools via @mcp.tool() decorators
  - keeps each tool to a thin wrapper over `src.data_loader` + `_stats.py`

Tool families:
  - telemetry : 6 per-tier tools (parameterized by tier + metric)
  - context   : 4 shared context tools
  - specials  : 3 per-tier specials
  - scenarios : 5 scenario/dataset tools
"""
