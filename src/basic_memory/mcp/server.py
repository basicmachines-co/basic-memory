"""
Basic Memory FastMCP server.
"""

from contextlib import asynccontextmanager

from fastmcp import FastMCP
from loguru import logger

from basic_memory import db
from basic_memory.mcp.container import McpContainer, set_container
from basic_memory.services.initialization import initialize_app
from basic_memory.telemetry import show_notice_if_needed, track_app_started


@asynccontextmanager
async def lifespan(app: FastMCP):
    """Lifecycle manager for the MCP server.

    Handles:
    - Database initialization and migrations
    - Telemetry notice and tracking
    - File sync via SyncCoordinator (if enabled and not in cloud mode)
    - Proper cleanup on shutdown
    """
    # --- Composition Root ---
    # Create container and read config (single point of config access)
    container = McpContainer.create()
    set_container(container)

    logger.info(f"Starting Basic Memory MCP server (mode={container.mode.name})")

    # Show telemetry notice (first run only) and track startup
    show_notice_if_needed()
    track_app_started("mcp")

    # Track if we created the engine (vs test fixtures providing it)
    # This prevents disposing an engine provided by test fixtures when
    # multiple Client connections are made in the same test
    engine_was_none = db._engine is None

    # Initialize app (runs migrations, reconciles projects)
    await initialize_app(container.config)

    # Create and start sync coordinator (lifecycle centralized in coordinator)
    sync_coordinator = container.create_sync_coordinator()
    await sync_coordinator.start()

    try:
        yield
    finally:
        # Shutdown - coordinator handles clean task cancellation
        logger.info("Shutting down Basic Memory MCP server")
        await sync_coordinator.stop()

        # Only shutdown DB if we created it (not if test fixture provided it)
        if engine_was_none:
            await db.shutdown_db()
            logger.info("Database connections closed")
        else:  # pragma: no cover
            logger.debug("Skipping DB shutdown - engine provided externally")


mcp = FastMCP(
    name="Basic Memory",
    lifespan=lifespan,
)
