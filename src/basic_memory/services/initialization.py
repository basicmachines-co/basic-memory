"""Shared initialization service for Basic Memory.

This module provides shared initialization functions used by both CLI and API
to ensure consistent application startup across all entry points.
"""

import asyncio
from pathlib import Path

from loguru import logger

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.repository import ProjectRepository


async def initialize_database(app_config: BasicMemoryConfig) -> None:
    """Initialize database with migrations handled automatically by get_or_create_db.

    Args:
        app_config: The Basic Memory project configuration

    Note:
        Database migrations are now handled automatically when the database
        connection is first established via get_or_create_db().
    """
    # Trigger database initialization and migrations by getting the database connection
    try:
        await db.get_or_create_db(app_config.database_path)
        logger.info("Database initialization completed")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        # Allow application to continue - it might still work
        # depending on what the error was, and will fail with a
        # more specific error if the database is actually unusable


async def reconcile_projects_with_config(app_config: BasicMemoryConfig):
    """Ensure all projects in config.json exist in the projects table and vice versa.

    This uses the ProjectService's synchronize_projects method to ensure bidirectional
    synchronization between the configuration file and the database.

    Args:
        app_config: The Basic Memory application configuration
    """
    logger.info("Reconciling projects from config with database...")

    # Get database session - migrations handled centrally
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
        ensure_migrations=False,
    )
    project_repository = ProjectRepository(session_maker)

    # Import ProjectService here to avoid circular imports
    from basic_memory.services.project_service import ProjectService

    try:
        # Create project service and synchronize projects
        project_service = ProjectService(repository=project_repository)
        await project_service.synchronize_projects()
        logger.info("Projects successfully reconciled between config and database")
    except Exception as e:
        # Log the error but continue with initialization
        logger.error(f"Error during project synchronization: {e}")
        logger.info("Continuing with initialization despite synchronization error")


async def initialize_file_sync(
    app_config: BasicMemoryConfig,
):
    """Initialize file synchronization services. This function starts the watch service and does not return

    Args:
        app_config: The Basic Memory project configuration

    Returns:
        The watch service task that's monitoring file changes
    """

    # delay import
    from basic_memory.sync import WatchService

    # Load app configuration - migrations handled centrally
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
        ensure_migrations=False,
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

    # First, sync all projects sequentially
    for project in active_projects:
        # avoid circular imports
        from basic_memory.cli.commands.sync import get_sync_service

        logger.info(f"Starting sync for project: {project.name}")
        sync_service = await get_sync_service(project)
        sync_dir = Path(project.path)

        try:
            await sync_service.sync(sync_dir, project_name=project.name)
            logger.info(f"Sync completed successfully for project: {project.name}")

            # Mark project as watching for changes after successful sync
            from basic_memory.services.sync_status_service import sync_status_tracker

            sync_status_tracker.start_project_watch(project.name)
            logger.info(f"Project {project.name} is now watching for changes")
        except Exception as e:  # pragma: no cover
            logger.error(f"Error syncing project {project.name}: {e}")
            # Mark sync as failed for this project
            from basic_memory.services.sync_status_service import sync_status_tracker

            sync_status_tracker.fail_project_sync(project.name, str(e))
            # Continue with other projects even if one fails

    # Then start the watch service in the background
    logger.info("Starting watch service for all projects")
    # run the watch service
    try:
        await watch_service.run()
        logger.info("Watch service started")
    except Exception as e:  # pragma: no cover
        logger.error(f"Error starting watch service: {e}")

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

    Args:
        app_config: The Basic Memory project configuration
    """
    try:
        result = asyncio.run(initialize_app(app_config))
        logger.info(f"Initialization completed successfully: result={result}")
    except Exception as e:  # pragma: no cover
        logger.exception(f"Error during initialization: {e}")
        # Continue execution even if initialization fails
        # The command might still work, or will fail with a
        # more specific error message
