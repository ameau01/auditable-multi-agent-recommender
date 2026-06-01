"""FastMCP server instance and tool registration.

Importing this module instantiates a single FastMCP and imports the four
tool family modules so their @mcp.tool() decorators register against this
instance. The CLI entry point (`__main__.py`) imports `mcp` from here and
calls `mcp.run()`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("cloud-optimization-telemetry")


# Importing the tool modules triggers their @mcp.tool() decorators, which
# register against the `mcp` instance above. Order does not matter; FastMCP
# tracks tools by name. We import them at module level (not inside a
# function) so the registration side-effect lands at import time.
from .tools import telemetry, context, specials, scenarios  # noqa: E402, F401
