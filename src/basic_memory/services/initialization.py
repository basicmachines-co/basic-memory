"""Shared initialization service for Basic Memory.

This module provides shared initialization functions used by both CLI and API
to ensure consistent application startup across all entry points.
"""

import asyncio
from typing import Optional

from loguru import logger

from basic_memory import db
from basic_memory.config import ProjectConfig, config_manager
from basic_memory.sync import WatchService

# Import this inside functions to avoid circular imports
# from basic_memory.cli.commands.sync import get_sync_service


async def initialize_database(app_config: ProjectConfig) -> None:
    """Run database migrations to ensure schema is up to date.

    Args:
        app_config: The Basic Memory project configuration
    """
    try:
        logger.info("Running database migrations...")
        await db.run_migrations(app_config)
        logger.info("Migrations completed successfully")
    except Exception as e:
        logger.error(f"Error running migrations: {e}")
        # Allow application to continue - it might still work
        # depending on what the error was, and will fail with a
        # more specific error if the database is actually unusable


async def initialize_file_sync(
    app_config: ProjectConfig,
) -> asyncio.Task:
    """Initialize file synchronization services.

    Args:
        app_config: The Basic Memory project configuration

    Returns:
        Tuple of (sync_service, watch_service, watch_task) if sync is enabled,
        or (None, None, None) if sync is disabled
    """
    # Load app configuration
    # Import here to avoid circular imports
    from basic_memory.cli.commands.sync import get_sync_service

    # Initialize sync service
    sync_service = await get_sync_service()

    # Initialize watch service
    watch_service = WatchService(
        sync_service=sync_service,
        file_service=sync_service.entity_service.file_service,
        config=app_config,
        quiet=True,
    )

    # Create the background task for running sync
    async def run_background_sync():  # pragma: no cover
        # Run initial full sync
        await sync_service.sync(app_config.home)
        logger.info("Sync completed successfully")

        # Start background sync task
        logger.info(f"Starting watch service to sync file changes in dir: {app_config.home}")

        # Start watching for changes
        await watch_service.run()

    watch_task = asyncio.create_task(run_background_sync())
    logger.info("Watch service started")
    return watch_task


async def initialize_app(
    app_config: ProjectConfig,
) -> Optional[asyncio.Task]:
    """Initialize the Basic Memory application.

    This function handles all initialization steps needed for both API and shor lived CLI commands.
    For long running commands like mcp, a
    - Running database migrations
    - Setting up file synchronization

    Args:
        app_config: The Basic Memory project configuration
    """
    # Initialize database first
    await initialize_database(app_config)

    basic_memory_config = config_manager.load_config()
    logger.info(f"Sync changes enabled: {basic_memory_config.sync_changes}")
    logger.info(
        f"Update permalinks on move enabled: {basic_memory_config.update_permalinks_on_move}"
    )
    if not basic_memory_config.sync_changes:  # pragma: no cover
        logger.info("Sync changes disabled. Skipping watch service.")
        return

    # Initialize file sync services
    return await initialize_file_sync(app_config)


def ensure_initialization(app_config: ProjectConfig) -> None:
    """Ensure initialization runs in a synchronous context.

    This is a wrapper for the async initialize_app function that can be
    called from synchronous code like CLI entry points.

    Args:
        app_config: The Basic Memory project configuration
    """
    try:
        asyncio.run(initialize_app(app_config))
    except Exception as e:
        logger.error(f"Error during initialization: {e}")
        # Continue execution even if initialization fails
        # The command might still work, or will fail with a
        # more specific error message


def ensure_initialize_database(app_config: ProjectConfig) -> None:
    """Ensure initialization runs in a synchronous context.

    This is a wrapper for the async initialize_database function that can be
    called from synchronous code like CLI entry points.

    Args:
        app_config: The Basic Memory project configuration
    """
    try:
        asyncio.run(initialize_database(app_config))
    except Exception as e:
        logger.error(f"Error during initialization: {e}")
        # Continue execution even if initialization fails
        # The command might still work, or will fail with a
        # more specific error message
