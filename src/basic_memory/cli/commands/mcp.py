"""MCP server command."""

import asyncio
from loguru import logger

import basic_memory
from basic_memory.cli.app import app
from basic_memory.config import config, config_manager
from basic_memory.services.initialization import initialize_app

# Import mcp instance
from basic_memory.mcp.server import mcp as mcp_server  # pragma: no cover

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: F401  # pragma: no cover


@app.command()
def mcp():  # pragma: no cover
    """Run the MCP server for Claude Desktop integration."""
    home_dir = config.home
    project_name = config.project

    # app config
    basic_memory_config = config_manager.load_config()

    logger.info(f"Starting Basic Memory MCP server {basic_memory.__version__}")
    logger.info(f"Project: {project_name}")
    logger.info(f"Project directory: {home_dir}")
    
    # Run initialization before starting MCP server
    # Note: Although we already run migrations via ensure_migrations in the CLI callback,
    # we do a full initialization here to ensure file sync is properly set up too
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(initialize_app(config))
    except Exception as e:
        logger.error(f"Error during app initialization: {e}")
        logger.info("Continuing with server startup despite initialization error")
    
    logger.info(f"Sync changes enabled: {basic_memory_config.sync_changes}")
    logger.info(
        f"Update permalinks on move enabled: {basic_memory_config.update_permalinks_on_move}"
    )

    mcp_server.run()
