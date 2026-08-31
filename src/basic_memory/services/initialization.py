"""Shared initialization service for Basic Memory.

This module provides shared initialization functions used by both CLI and API
to ensure consistent application startup across all entry points.
"""

import asyncio
import os
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING


from loguru import logger

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.models import Project
from basic_memory.repository import (
    ProjectRepository,
)
from basic_memory.utils import generate_permalink

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from basic_memory.index.local_project import LocalProjectIndexRuntimeProvider
    from basic_memory.read_cache import ReadCache, ReadCacheInvalidator


async def run_initial_project_index(
    project: Project,
    *,
    runtime_factory: "LocalProjectIndexRuntimeProvider",
) -> None:
    """Run startup project indexing through the local project-index fanout runtime."""
    from basic_memory.index.local_project import run_local_project_index_for_project

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


async def recover_project_materializations(
    project: Project,
    session_maker: "async_sessionmaker[AsyncSession]",
    *,
    read_cache: "ReadCacheInvalidator | None" = None,
) -> None:
    """Re-drive note materialization and move cleanup lost across a process exit.

    A local note write returns before its markdown file is written; a crash
    between accepting the DB snapshot and writing the file leaves note_content
    stuck in writing/pending (a transient write error leaves it failed) and the
    source-of-truth file missing. A moved note can also synchronize its destination
    before the in-memory source cleanup is lost. Runs once at startup, before the
    initial index, so both durable states converge. Non-fatal: a recovery failure
    must not block startup, so it is logged and startup continues.
    """
    from basic_memory.index.note_content_materialization import (
        recover_move_vacates,
        recover_stuck_materializations,
    )
    from basic_memory.read_cache import invalidate_cache
    from basic_memory.services.file_service import FileService

    # FileService needs only base_path to write the accepted markdown bytes;
    # the markdown_processor/app_config are unused on the materialization path.
    file_service = FileService(Path(project.path))
    # Recovery reports whether it published state only after its transaction-bearing
    # phase exits. Scope the whole phase so cancellation cannot land in that window;
    # a harmless generation bump after a no-op recovery is the correctness tradeoff.
    materialization_scope = (
        invalidate_cache(read_cache, str(project.external_id))
        if read_cache is not None
        else nullcontext()
    )
    async with materialization_scope:
        try:
            materialization_recovery = await recover_stuck_materializations(
                session_maker=session_maker,
                file_service=file_service,
                project_id=project.id,
            )
        except Exception as e:  # pragma: no cover - defensive startup guard
            logger.error(f"Error recovering stuck materializations for project {project.name}: {e}")
            return

    if materialization_recovery.attempted:
        logger.info(
            "Recovered note materialization state on startup",
            project=project.name,
            attempted_materializations=materialization_recovery.attempted,
            recovered_materializations=materialization_recovery.written,
        )

    vacate_scope = (
        invalidate_cache(read_cache, str(project.external_id))
        if read_cache is not None
        else nullcontext()
    )
    async with vacate_scope:
        try:
            recovered_vacates = await recover_move_vacates(
                session_maker=session_maker,
                file_service=file_service,
                project_id=project.id,
            )
        except Exception as e:  # pragma: no cover - defensive startup guard
            logger.error(f"Error recovering move vacates for project {project.name}: {e}")
            return

    if not recovered_vacates:
        return

    logger.info(
        "Recovered note move-vacate state on startup",
        project=project.name,
        recovered_move_vacates=recovered_vacates,
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


async def reconcile_projects_with_config(app_config: BasicMemoryConfig) -> bool:
    """Ensure all projects in config.json exist in the projects table and vice versa.

    Returns True when synchronization completed, False when it failed (the
    failure is logged; startup continues either way).

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
    except Exception as e:
        logger.error(f"Error during project synchronization: {e}")
        logger.info("Continuing with initialization despite synchronization error")
        return False
    logger.info("Projects successfully reconciled between config and database")
    return True


# Database paths this process has already reconciled config projects into.
_reconciled_database_paths: set[Path] = set()


async def reconcile_projects_with_config_once(app_config: BasicMemoryConfig) -> None:
    """Reconcile config projects into the database the first time a process opens it.

    Trigger: a request opens the local database outside a server lifespan — the
    one-shot CLI and the MCP local flow drive the API over an in-process ASGI
    transport, so nothing else seeds config.json's projects into a fresh database.
    Why: initialize_app() only runs for the API/MCP servers and a few CLI
    commands, so a CLI-only fresh install had `main` in config.json but no
    projects row, and every default-project command failed while
    `project add main` refused with "already exists" (#1334, regression of #974).
    Outcome: the same reconciliation the servers run, once per process; cloud and
    stateless deployments stay untouched, matching initialize_app().
    """
    if app_config.skip_local_initialization:
        return
    database_path = Path(app_config.database_path)
    if database_path in _reconciled_database_paths:
        return
    # Only a completed reconciliation retires this path; a transient failure
    # (logged inside) leaves the next request to try again rather than pinning
    # a long-lived MCP process to an unseeded database.
    if await reconcile_projects_with_config(app_config):
        _reconciled_database_paths.add(database_path)


# Strong references for fire-and-forget startup index tasks; the event loop
# alone would hold only weak references (asyncio.create_task docs).
_initial_index_tasks: set[asyncio.Task[None]] = set()


async def initialize_file_indexing(
    app_config: BasicMemoryConfig,
    quiet: bool = True,
    *,
    recovery_complete: asyncio.Event | None = None,
    read_cache: "ReadCache | None" = None,
) -> None:
    """Initialize file indexing services.

    This function starts the watch service and does not return.

    Args:
        app_config: The Basic Memory project configuration
        quiet: Whether to suppress Rich console output (True for MCP, False for CLI watch)
        recovery_complete: Optional startup barrier set after durable recovery finishes.
        read_cache: Optional host-injected semantic read cache.

    Returns:
        The watch service task that's monitoring file changes
    """
    # Never start file watching during tests. Even "background" watchers add tasks/threads
    # and can interact badly with strict asyncio teardown (especially on Windows/aiosqlite).
    # Skip file indexing in test environments to avoid interference with tests
    if app_config.is_test_env:
        logger.info("Test environment detected - skipping file indexing initialization")
        if recovery_complete is not None:
            recovery_complete.set()
        return None

    # delay import
    from basic_memory.index.local_project import LocalProjectIndexRuntimeFactory
    from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
    from basic_memory.index.watch_service import WatchService

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
        read_cache=read_cache,
    )
    project_index_runtime_factory = LocalProjectIndexRuntimeFactory(
        read_cache=read_cache,
    )

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
        constrained_permalink = generate_permalink(constrained_project)
        active_projects = [p for p in active_projects if p.permalink == constrained_permalink]
        logger.info(f"Background indexing constrained to project: {constrained_project}")

    # Only index projects that are in config (source of truth) and have an
    # absolute local path; see BasicMemoryConfig.is_locally_syncable. This keeps
    # background indexing from adopting the process cwd as a project root and
    # mutating unrelated files (issue #949).
    skip = [p.name for p in active_projects if not app_config.is_locally_syncable(p.name, p.path)]
    if skip:
        active_projects = [p for p in active_projects if p.name not in skip]
        logger.info(f"Skipping projects that are not locally indexable: {skip}")

    # Recover materializations left stuck (writing/pending/failed) and source
    # cleanup whose in-process enqueue was lost. Runs synchronously so the files
    # converge before the initial project index scans them.
    for project in active_projects:
        await recover_project_materializations(
            project,
            session_maker,
            read_cache=read_cache,
        )

    # Trigger: the API/MCP lifespan is waiting for durable startup recovery.
    # Why: serving accepted writes while recovery holds cached vacate work can
    # delete a path that a concurrent move has just made live again.
    # Outcome: release startup before background indexing and watching begin.
    if recovery_complete is not None:
        recovery_complete.set()

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

    # Create background tasks for all project indexes (non-blocking). The event
    # loop keeps only weak task references, so hold them in the module-level set
    # to keep GC from cancelling an index mid-flight.
    for project in active_projects:
        index_task = asyncio.create_task(index_project_background(project))
        _initial_index_tasks.add(index_task)
        index_task.add_done_callback(_initial_index_tasks.discard)
    logger.info(f"Created {len(active_projects)} background indexing tasks")

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

    # Trigger: cloud/stateless deployment (skip_local_initialization — either
    # for_cloud_tenant's skip_initialization_sync or BASIC_MEMORY_CLOUD_MODE).
    # Why: cloud manages its own schema and per-tenant projects from the database.
    # Running reconcile_projects_with_config there would delete tenant project rows
    # absent from local config. Gating on the Postgres *backend* was wrong (it
    # caught a LOCAL Postgres install, which still needs the seeded default
    # reconciled into a projects row, else /v2/projects/resolve rejects it).
    # Outcome: skip only for actual cloud/stateless deployments.
    if app_config.skip_local_initialization:
        logger.info(
            "Skipping local initialization - cloud/stateless deployment manages its own schema"
        )
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

    No-op for cloud/stateless deployments (skip_local_initialization). A LOCAL
    Postgres install still needs initialization, so gate on that, not the backend —
    matching initialize_app.

    Args:
        app_config: The Basic Memory project configuration
    """
    if app_config.skip_local_initialization:
        logger.info(
            "Skipping local initialization - cloud/stateless deployment manages its own schema"
        )
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

    asyncio.run(_init_and_cleanup())
    logger.info("Initialization completed successfully")
