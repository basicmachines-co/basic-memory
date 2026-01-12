"""Shared initialization service for Basic Memory.

This module provides shared initialization functions used by both CLI and API
to ensure consistent application startup across all entry points.
"""

import asyncio
import os
import sys
from pathlib import Path


from loguru import logger

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.models import Project
from basic_memory.repository import (
    ProjectRepository,
)


async def initialize_database(app_config: BasicMemoryConfig) -> None:
    """Initialize database with migrations handled automatically by get_or_create_db.

    Args:
        app_config: The Basic Memory project configuration

    Note:
        Database migrations are now handled automatically when the database
        connection is first established via get_or_create_db().
    """
    try:
        await db.get_or_create_db(app_config.database_path)
        logger.info("Database initialization completed")
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        raise


async def reconcile_projects_with_config(app_config: BasicMemoryConfig):
    """Ensure all projects in config.json exist in the projects table and vice versa.

    This uses the ProjectService's synchronize_projects method to ensure bidirectional
    synchronization between the configuration file and the database.

    Args:
        app_config: The Basic Memory application configuration
    """
    logger.info("Reconciling projects from config with database...")

    # Get database session (engine already created by initialize_database)
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )
    project_repository = ProjectRepository(session_maker)

    # Import ProjectService here to avoid circular imports
    from basic_memory.services.project_service import ProjectService

    # Create project service and synchronize projects
    project_service = ProjectService(repository=project_repository)
    try:
        await project_service.synchronize_projects()
        logger.info("Projects successfully reconciled between config and database")
    except Exception as e:
        logger.error(f"Error during project synchronization: {e}")
        logger.info("Continuing with initialization despite synchronization error")


async def initialize_file_sync(
    app_config: BasicMemoryConfig,
) -> None:
    """Initialize file synchronization services. This function starts the watch service and does not return

    Args:
        app_config: The Basic Memory project configuration

    Returns:
        The watch service task that's monitoring file changes
    """
    # Never start file watching during tests. Even "background" watchers add tasks/threads
    # and can interact badly with strict asyncio teardown (especially on Windows/aiosqlite).
    # Skip file sync in test environments to avoid interference with tests
    if app_config.is_test_env:
        logger.info("Test environment detected - skipping file sync initialization")
        return None

    # delay import
    from basic_memory.sync import WatchService

    # Get database session (migrations already run if needed)
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )
    project_repository = ProjectRepository(session_maker)

    # Initialize watch service
    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        quiet=True,
    )

    # Get active projects
    active_projects = await project_repository.get_active_projects()

    # Filter to constrained project if MCP server was started with --project
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project:
        active_projects = [p for p in active_projects if p.name == constrained_project]
        logger.info(f"Background sync constrained to project: {constrained_project}")

    # Start sync for all projects as background tasks (non-blocking)
    async def sync_project_background(project: Project):
        """Sync a single project in the background."""
        # avoid circular imports
        from basic_memory.sync.sync_service import get_sync_service

        logger.info(f"Starting background sync for project: {project.name}")
        try:
            # Create sync service
            sync_service = await get_sync_service(project)

            sync_dir = Path(project.path)
            await sync_service.sync(sync_dir, project_name=project.name)
            logger.info(f"Background sync completed successfully for project: {project.name}")
        except Exception as e:  # pragma: no cover
            logger.error(f"Error in background sync for project {project.name}: {e}")

    # Create background tasks for all project syncs (non-blocking)
    sync_tasks = [
        asyncio.create_task(sync_project_background(project)) for project in active_projects
    ]
    logger.info(f"Created {len(sync_tasks)} background sync tasks")

    # Don't await the tasks - let them run in background while we continue

    # Then start the watch service in the background
    logger.info("Starting watch service for all projects")

    # run the watch service
    await watch_service.run()
    logger.info("Watch service started")

    return None


async def initialize_app(
    app_config: BasicMemoryConfig,
):
    """Initialize the Basic Memory application.

    This function handles all initialization steps:
    - Running database migrations
    - Reconciling projects from config.json with projects table
    - Setting up file synchronization
    - Starting background migration for legacy project data

    Args:
        app_config: The Basic Memory project configuration
    """
    # Skip initialization in cloud mode - cloud manages its own projects
    if app_config.cloud_mode_enabled:
        logger.debug("Skipping initialization in cloud mode - projects managed by cloud")
        return

    logger.info("Initializing app...")
    # Initialize database first
    await initialize_database(app_config)

    # Reconcile projects from config.json with projects table
    await reconcile_projects_with_config(app_config)

    logger.info("App initialization completed (migration running in background if needed)")


def ensure_initialization(app_config: BasicMemoryConfig) -> None:
    """Ensure initialization runs in a synchronous context.

    This is a wrapper for the async initialize_app function that can be
    called from synchronous code like CLI entry points.

    No-op if app_config.cloud_mode == True. Cloud basic memory manages it's own projects

    Args:
        app_config: The Basic Memory project configuration
    """
    # Skip initialization in cloud mode - cloud manages its own projects
    if app_config.cloud_mode_enabled:
        logger.debug("Skipping initialization in cloud mode - projects managed by cloud")
        return

    async def _init_and_cleanup():
        """Initialize app and clean up database connections.

        Database connections created during initialization must be cleaned up
        before the event loop closes, otherwise the process will hang indefinitely.
        """
        try:
            await initialize_app(app_config)
        finally:
            # Always cleanup database connections to prevent process hang
            await db.shutdown_db()

    # On Windows, use SelectorEventLoop to avoid ProactorEventLoop cleanup issues
    # The ProactorEventLoop can raise "IndexError: pop from an empty deque" during
    # event loop cleanup when there are pending handles. SelectorEventLoop is more
    # stable for our use case (no subprocess pipes or named pipes needed).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(_init_and_cleanup())
    logger.info("Initialization completed successfully")
