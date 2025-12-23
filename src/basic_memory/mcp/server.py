"""
Basic Memory FastMCP server.
"""

import asyncio
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from loguru import logger

from basic_memory import db
from basic_memory.config import ConfigManager
from basic_memory.services.initialization import initialize_app, initialize_file_sync


@asynccontextmanager
async def lifespan(app: FastMCP):
    """Lifecycle manager for the MCP server.

    Handles:
    - Database initialization and migrations
    - File sync in background (if enabled and not in cloud mode)
    - Proper cleanup on shutdown
    """
    app_config = ConfigManager().config
    logger.info("Starting Basic Memory MCP server")

    # Initialize app (runs migrations, reconciles projects)
    await initialize_app(app_config)

    # Start file sync as background task (if enabled and not in cloud mode)
    sync_task = None
    if app_config.sync_changes and not app_config.cloud_mode_enabled:
        logger.info("Starting file sync in background")
        sync_task = asyncio.create_task(initialize_file_sync(app_config))
    elif app_config.cloud_mode_enabled:
        logger.info("Cloud mode enabled - skipping local file sync")
    else:
        logger.info("Sync changes disabled - skipping file sync")

    try:
        yield
    finally:
        # Shutdown
        logger.info("Shutting down Basic Memory MCP server")
        if sync_task:
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                logger.info("File sync task cancelled")

        await db.shutdown_db()
        logger.info("Database connections closed")


mcp = FastMCP(
    name="Basic Memory",
    lifespan=lifespan,
)
