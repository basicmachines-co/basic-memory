"""MCP server command."""

from loguru import logger
from basic_memory.cli.app import app
from basic_memory.config import config

# Import mcp instance
from basic_memory.mcp.server import mcp as mcp_server  # pragma: no cover

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: F401  # pragma: no cover


@app.command()
def mcp():  # pragma: no cover
    """Run the MCP server for Claude Desktop integration."""
    home_dir = config.home
    logger.info("Starting Basic Memory MCP server")
    logger.info(f"Home directory: {home_dir}")
    mcp_server.run()
