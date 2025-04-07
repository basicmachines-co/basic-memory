"""MCP server command."""

import basic_memory
from basic_memory.cli.app import app

# Import mcp instance
from basic_memory.mcp.server import mcp as mcp_server  # pragma: no cover

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: F401  # pragma: no cover


@app.command()
def mcp():  # pragma: no cover
    """Run the MCP server"""
    from basic_memory.config import config
    import asyncio
    from basic_memory.services.initialization import initialize_database

    # First, run just the database migrations synchronously
    asyncio.run(initialize_database(config))

    # Load config to check if sync is enabled
    from basic_memory.config import config_manager

    basic_memory_config = config_manager.load_config()

    if basic_memory_config.sync_changes:
        # For now, we'll just log that sync will be handled by the MCP server
        from loguru import logger

        logger.info("File sync will be handled by the MCP server")

    # Start the MCP server
    mcp_server.run()
