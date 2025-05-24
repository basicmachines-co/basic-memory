"""Shared initialization service for Basic Memory.

This module provides shared initialization functions used by both CLI and API
to ensure consistent application startup across all entry points.
"""

import asyncio
import shutil
from pathlib import Path

from loguru import logger

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.models import Project
from basic_memory.repository import ProjectRepository


async def initialize_database(app_config: BasicMemoryConfig) -> None:
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


async def reconcile_projects_with_config(app_config: BasicMemoryConfig):
    """Ensure all projects in config.json exist in the projects table and vice versa.

    This uses the ProjectService's synchronize_projects method to ensure bidirectional
    synchronization between the configuration file and the database.

    Args:
        app_config: The Basic Memory application configuration
    """
    logger.info("Reconciling projects from config with database...")

    # Get database session
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
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


async def migrate_legacy_projects(app_config: BasicMemoryConfig):
    # Get database session
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
    )
    logger.info("Migrating legacy projects...")
    project_repository = ProjectRepository(session_maker)

    # For each project in config.json, check if it has a .basic-memory dir
    for project_name, project_path in app_config.projects.items():
        legacy_dir = Path(project_path) / ".basic-memory"
        if not legacy_dir.exists():
            continue
        logger.info(f"Detected legacy project directory: {legacy_dir}")
        project = await project_repository.get_by_name(project_name)
        if not project:  # pragma: no cover
            logger.error(f"Project {project_name} not found in database, skipping migration")
            continue

        await migrate_legacy_project_data(project, legacy_dir)
    logger.info("Legacy projects successfully migrated")


async def migrate_legacy_project_data(project: Project, legacy_dir: Path) -> bool:
    """Check if project has legacy .basic-memory dir and migrate if needed.

    Args:
        project: The project to check and potentially migrate

    Returns:
        True if migration occurred, False otherwise
    """

    # avoid circular imports
    from basic_memory.cli.commands.sync import get_sync_service

    sync_service = await get_sync_service(project)
    sync_dir = Path(project.path)

    logger.info(f"Sync starting project: {project.name}")
    await sync_service.sync(sync_dir)
    logger.info(f"Sync completed successfully for project: {project.name}")

    # After successful sync, remove the legacy directory
    try:
        logger.info(f"Removing legacy directory: {legacy_dir}")
        shutil.rmtree(legacy_dir)
        return True
    except Exception as e:
        logger.error(f"Error removing legacy directory: {e}")
        return False


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

    # Load app configuration
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
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
            await sync_service.sync(sync_dir)
            logger.info(f"Sync completed successfully for project: {project.name}")
        except Exception as e:  # pragma: no cover
            logger.error(f"Error syncing project {project.name}: {e}")
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
    - Migrating legacy project data

    Args:
        app_config: The Basic Memory project configuration
    """
    logger.info("Initializing app...")
    # Initialize database first
    await initialize_database(app_config)

    # Reconcile projects from config.json with projects table
    await reconcile_projects_with_config(app_config)

    # migrate legacy project data
    await migrate_legacy_projects(app_config)


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
