"""
Basic Memory FastMCP server.
"""

import asyncio
import os
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

    # Track if we created the engine (vs test fixtures providing it)
    # This prevents disposing an engine provided by test fixtures when
    # multiple Client connections are made in the same test
    engine_was_none = db._engine is None

    # Initialize app (runs migrations, reconciles projects)
    await initialize_app(app_config)

    # Start file sync as background task (if enabled and not in cloud mode)
    sync_task = None
    is_test_env = (
        app_config.env == "test"
        or os.getenv("BASIC_MEMORY_ENV", "").lower() == "test"
        or os.getenv("PYTEST_CURRENT_TEST") is not None
    )
    if is_test_env:
        logger.info("Test environment detected - skipping local file sync")
    elif app_config.sync_changes and not app_config.cloud_mode_enabled:
        logger.info("Starting file sync in background")

        async def _file_sync_runner() -> None:
            await initialize_file_sync(app_config)

        sync_task = asyncio.create_task(_file_sync_runner())
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

        # Only shutdown DB if we created it (not if test fixture provided it)
        if engine_was_none:
            await db.shutdown_db()
            logger.info("Database connections closed")
        else:
            logger.debug("Skipping DB shutdown - engine provided externally")


mcp = FastMCP(
    name="Basic Memory",
    lifespan=lifespan,
)
