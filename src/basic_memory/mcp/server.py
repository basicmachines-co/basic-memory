"""Enhanced FastMCP server instance for Basic Memory."""

from mcp.server.fastmcp import FastMCP

from basic_memory.utils import setup_logging

# mcp console logging
# configure_logging(level='INFO')


# start our out file logging
setup_logging(log_file=".basic-memory/basic-memory.log")

# Create the shared server instance
mcp = FastMCP("Basic Memory")
