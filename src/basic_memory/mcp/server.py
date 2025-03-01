"""Enhanced FastMCP server instance for Basic Memory."""

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.logging import configure_logging

# mcp console logging
configure_logging(level="INFO")


# Create the shared server instance
mcp = FastMCP("Basic Memory")
