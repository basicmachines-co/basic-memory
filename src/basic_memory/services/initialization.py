"""Shared initialization service for Basic Memory.

This module provides shared initialization functions used by both CLI and API
to ensure consistent application startup across all entry points.
"""

import asyncio
import os
import sys
from typing import TYPE_CHECKING, Protocol


from loguru import logger

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.models import Project
from basic_memory.repository import (
    ProjectRepository,
)
from basic_memory.runtime import TenantId

if TYPE_CHECKING:
    from basic_memory.index import LocalProjectIndexRuntime


class InitialProjectIndexRuntimeFactory(Protocol):
    """Build local project-index runtime dependencies for startup indexing."""

    tenant_id: TenantId

    async def runtime_for_project(self, project: Project) -> "LocalProjectIndexRuntime": ...


async def run_initial_project_index(
    project: Project,
    *,
    runtime_factory: InitialProjectIndexRuntimeFactory,
) -> None:
    """Run startup project indexing through the local project-index fanout runtime."""
    from basic_memory.index import run_local_project_index_for_project

    result = await run_local_project_index_for_project(
        project,
        runtime_factory=runtime_factory,
    )
    logger.info(
        "Initial project-index fanout completed",
        f"project={project.name}",
        f"total_files={result.total_files}",
        f"enqueued_files={result.enqueued_files}",
        f"enqueued_batches={result.enqueued_batches}",
        f"deleted_files={result.deleted_files}",
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
    project_repository = ProjectRepository()

    # Import ProjectService here to avoid circular imports
    from basic_memory.services.project_service import ProjectService

    # Create project service and synchronize projects
    project_service = ProjectService(repository=project_repository, session_maker=session_maker)
    try:
        await project_service.synchronize_projects()
        logger.info("Projects successfully reconciled between config and database")
    except Exception as e:
        logger.error(f"Error during project synchronization: {e}")
        logger.info("Continuing with initialization despite synchronization error")


async def initialize_file_indexing(
    app_config: BasicMemoryConfig,
    quiet: bool = True,
) -> None:
    """Initialize file indexing services.

    This function starts the watch service and does not return.

    Args:
        app_config: The Basic Memory project configuration
        quiet: Whether to suppress Rich console output (True for MCP, False for CLI watch)

    Returns:
        The watch service task that's monitoring file changes
    """
    # Never start file watching during tests. Even "background" watchers add tasks/threads
    # and can interact badly with strict asyncio teardown (especially on Windows/aiosqlite).
    # Skip file indexing in test environments to avoid interference with tests
    if app_config.is_test_env:
        logger.info("Test environment detected - skipping file indexing initialization")
        return None

    # delay import
    from basic_memory.index import (
        LocalProjectIndexRuntimeFactory,
        LocalWatchEventIndexRuntimeFactory,
        WatchService,
    )

    # Get database session (migrations already run if needed)
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )
    project_repository = ProjectRepository()

    # Filter to constrained project if MCP server was started with --project.
    # Applied to both the initial background indexing and the watch service so that
    # running multiple `basic-memory mcp --project X` processes does not produce
    # duplicate watchers fighting over the same files.
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")

    event_index_runtime_factory = LocalWatchEventIndexRuntimeFactory(
        index_embeddings=app_config.semantic_search_enabled,
    )
    project_index_runtime_factory = LocalProjectIndexRuntimeFactory()

    # Initialize watch service
    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        quiet=quiet,
        event_index_runtime_factory=event_index_runtime_factory,
        constrained_project=constrained_project,
    )

    # Get active projects
    async with db.scoped_session(session_maker) as session:
        active_projects = await project_repository.get_active_projects(session)

    if constrained_project:
        active_projects = [p for p in active_projects if p.name == constrained_project]
        logger.info(f"Background indexing constrained to project: {constrained_project}")

    # Only index projects that are in config (source of truth) and have an
    # absolute local path; see BasicMemoryConfig.is_locally_syncable. This keeps
    # background indexing from adopting the process cwd as a project root and
    # mutating unrelated files (issue #949).
    skip = [p.name for p in active_projects if not app_config.is_locally_syncable(p.name, p.path)]
    if skip:
        active_projects = [p for p in active_projects if p.name not in skip]
        logger.info(f"Skipping projects that are not locally indexable: {skip}")

    # Start indexing for all projects as background tasks (non-blocking)
    async def index_project_background(project: Project):
        """Index a single project in the background."""
        logger.info(f"Starting background project index for project: {project.name}")
        try:
            await run_initial_project_index(
                project,
                runtime_factory=project_index_runtime_factory,
            )
            logger.info(f"Background project index completed for project: {project.name}")
        except Exception as e:  # pragma: no cover
            logger.error(f"Error in background project index for project {project.name}: {e}")

    # Create background tasks for all project indexes (non-blocking)
    index_tasks = [
        asyncio.create_task(index_project_background(project)) for project in active_projects
    ]
    logger.info(f"Created {len(index_tasks)} background indexing tasks")

    # Don't await the tasks - let them run in background while we continue

    # Then start the watch service in the background
    if constrained_project:
        logger.info(f"Starting watch service constrained to project: {constrained_project}")
    else:
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
    - Setting up file indexing
    - Starting background migration for legacy project data

    Args:
        app_config: The Basic Memory project configuration
    """
    # Trigger: frontmatter enforcement is enabled while permalink generation is disabled
    # Why: missing-frontmatter indexing needs canonical permalinks for deterministic output
    # Outcome: log startup precedence so behavior is explicit to operators
    if app_config.ensure_frontmatter_on_sync and app_config.disable_permalinks:
        logger.warning(
            "Config precedence: ensure_frontmatter_on_sync=True overrides "
            "disable_permalinks=True for markdown files missing frontmatter during indexing; "
            "permalinks will be written."
        )

    # Trigger: database backend is Postgres (cloud deployment)
    # Why: cloud deployments manage their own projects and migrations via the cloud platform.
    # The local MCP server always uses SQLite and needs initialization even when
    # projects are configured for cloud routing.
    # Outcome: skip initialization only for actual cloud Postgres deployments.
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        logger.info("Skipping local initialization - Postgres backend manages its own schema")
        return

    logger.info("Initializing app...")
    # Initialize database first
    await initialize_database(app_config)

    # Reconcile projects from config.json with projects table
    await reconcile_projects_with_config(app_config)

    logger.info("App initialization completed")


def ensure_initialization(app_config: BasicMemoryConfig) -> None:
    """Ensure initialization runs in a synchronous context.

    This is a wrapper for the async initialize_app function that can be
    called from synchronous code like CLI entry points.

    No-op if database backend is Postgres (cloud deployment manages its own schema).

    Args:
        app_config: The Basic Memory project configuration
    """
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        logger.info("Skipping local initialization - Postgres backend manages its own schema")
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
