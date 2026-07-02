"""Watch service for Basic Memory."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import (
    AsyncIterator,
    List,
    Optional,
    Set,
    Sequence,
    Callable,
    Awaitable,
    TYPE_CHECKING,
    Protocol,
)

if TYPE_CHECKING:
    from basic_memory.sync.sync_service import SyncService

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, WATCH_STATUS_JSON
from basic_memory.ignore_utils import load_gitignore_patterns
from basic_memory.index import (
    LocalWatchEventIndexRequest,
    StorageEventIndexRuntime,
    local_project_root,
    local_watch_filter_roots,
    local_watch_path_is_observable,
    local_watch_path_is_under_project,
    local_watch_project_change_batches,
    plan_local_watch_event_index_status_update,
    run_local_watch_event_indexing,
)
from basic_memory.models import Project
from basic_memory.repository import ProjectRepository
from loguru import logger
from pydantic import BaseModel, Field
from rich.console import Console
from watchfiles import awatch
from watchfiles.main import FileChange, Change
import time

SYNC_WATCH_SERVICE_DEPRECATION_MESSAGE = (
    "basic_memory.sync.WatchService is deprecated reference-only legacy watcher code. "
    "Use basic_memory.index.WatchService for local event-based indexing."
)


class WatchEvent(BaseModel):
    timestamp: datetime
    path: str
    action: str  # new, delete, etc
    status: str  # success, error
    checksum: Optional[str]
    error: Optional[str] = None


class WatchServiceState(BaseModel):
    # Service status
    running: bool = False
    start_time: datetime = Field(default_factory=datetime.now)
    pid: int = Field(default_factory=os.getpid)

    # Stats
    error_count: int = 0
    last_error: Optional[datetime] = None
    last_scan: Optional[datetime] = None

    # File counts
    synced_files: int = 0

    # Recent activity
    recent_events: List[WatchEvent] = Field(default_factory=list)

    def add_event(
        self,
        path: str,
        action: str,
        status: str,
        checksum: Optional[str] = None,
        error: Optional[str] = None,
    ) -> WatchEvent:
        event = WatchEvent(
            timestamp=datetime.now(),
            path=path,
            action=action,
            status=status,
            checksum=checksum,
            error=error,
        )
        self.recent_events.insert(0, event)
        self.recent_events = self.recent_events[:100]  # Keep last 100
        return event

    def record_error(self, error: str):
        self.error_count += 1
        self.add_event(path="", action="sync", status="error", error=error)
        self.last_error = datetime.now()


# Type alias for sync service factory function
SyncServiceFactory = Callable[[Project], Awaitable["SyncService"]]


class WatchEventIndexRuntimeFactory(Protocol):
    """Build event-index runtime dependencies for one watched project."""

    async def runtime_for_project(self, project: Project) -> StorageEventIndexRuntime: ...


class WatchService:
    """Deprecated reference-only watcher retained while callers move to basic_memory.index."""

    __deprecated__ = SYNC_WATCH_SERVICE_DEPRECATION_MESSAGE

    def __init__(
        self,
        app_config: BasicMemoryConfig,
        project_repository: ProjectRepository,
        session_maker: async_sessionmaker[AsyncSession],
        quiet: bool = False,
        sync_service_factory: Optional[SyncServiceFactory] = None,
        event_index_runtime_factory: Optional[WatchEventIndexRuntimeFactory] = None,
        constrained_project: Optional[str] = None,
    ):
        self.app_config = app_config
        self.project_repository = project_repository
        self.session_maker = session_maker
        self.state = WatchServiceState()
        self.status_path = app_config.data_dir_path / WATCH_STATUS_JSON
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self._ignore_patterns_cache: dict[Path, Set[str]] = {}
        self._sorted_watch_filter_roots: tuple[Path, ...] | None = None
        self._sync_service_factory = sync_service_factory
        self._event_index_runtime_factory = event_index_runtime_factory
        if (
            self._event_index_runtime_factory is None
            and self._sync_service_factory is None
            and app_config.watch_event_index
        ):
            from basic_memory.index import LocalWatchEventIndexRuntimeFactory

            self._event_index_runtime_factory = LocalWatchEventIndexRuntimeFactory()
        # When set (typically from BASIC_MEMORY_MCP_PROJECT), the watch cycle
        # only observes this project. Without it, each `basic-memory mcp --project X`
        # process spawns a watcher over every project and racing writers collide
        # on the same files.
        self.constrained_project = constrained_project

        # quiet mode for mcp so it doesn't mess up stdout
        self.console = Console(quiet=quiet)

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[AsyncSession]:
        """Open a service-owned transaction."""
        async with db.scoped_session(self.session_maker) as session:
            yield session

    async def _get_sync_service(self, project: Project) -> "SyncService":
        """Get sync service for a project, using factory if provided."""
        if self._sync_service_factory:
            return await self._sync_service_factory(project)
        # Fall back to default factory
        from basic_memory.sync.sync_service import get_sync_service

        return await get_sync_service(project)

    async def _schedule_restart(self, stop_event: asyncio.Event):
        """Schedule a restart of the watch service after the configured interval."""
        await asyncio.sleep(self.app_config.watch_project_reload_interval)
        stop_event.set()

    def _get_ignore_patterns(self, project_path: Path) -> Set[str]:
        """Get or load ignore patterns for a project path."""
        if project_path not in self._ignore_patterns_cache:
            self._ignore_patterns_cache[project_path] = load_gitignore_patterns(project_path)
        return self._ignore_patterns_cache[project_path]

    async def _watch_projects_cycle(self, projects: Sequence[Project], stop_event: asyncio.Event):
        """Run one cycle of watching the given projects until stop_event is set."""
        project_paths = [project.path for project in projects]
        previous_filter_roots = self._sorted_watch_filter_roots
        self._sorted_watch_filter_roots = local_watch_filter_roots(projects)

        try:
            async for changes in awatch(
                *project_paths,
                debounce=self.app_config.sync_delay,
                watch_filter=self.filter_changes,
                recursive=True,
                stop_event=stop_event,
            ):
                ignore_patterns_by_project_root = {
                    local_project_root(project): self._get_ignore_patterns(
                        local_project_root(project)
                    )
                    for project in projects
                }
                project_changes = local_watch_project_change_batches(
                    projects=projects,
                    changes=changes,
                    ignore_patterns_by_project_root=ignore_patterns_by_project_root,
                )

                # create coroutines to handle changes
                change_handlers = [
                    self.handle_changes(batch.project, set(batch.changes))
                    for batch in project_changes
                ]

                # process changes
                await asyncio.gather(*change_handlers)
        finally:
            self._sorted_watch_filter_roots = previous_filter_roots

    async def _select_projects_to_watch(self) -> list[Project]:
        """Return the set of projects this watch cycle should observe.

        Applies two filters in order:
          1. ``constrained_project`` — if the MCP server was started with
             ``--project``, only that project is watched. This keeps concurrent
             MCP processes from producing duplicate watchers that race on the
             same files.
          2. Projects that are not locally syncable are skipped — those missing
             from config (config is the source of truth, so stale DB rows must
             not be watched) or with a non-absolute path (which would resolve
             against the process cwd and make the watcher observe and mutate the
             directory the server was launched from). See
             ``BasicMemoryConfig.is_locally_syncable`` (issue #949). Cloud
             projects with a local bisync copy keep their absolute path and are
             still watched.
        """
        async with self._session_scope() as session:
            projects = await self.project_repository.get_active_projects(session)

        if self.constrained_project:
            projects = [p for p in projects if p.name == self.constrained_project]

        skip = [p.name for p in projects if not self.app_config.is_locally_syncable(p.name, p.path)]
        if skip:
            projects = [p for p in projects if p.name not in skip]
            logger.debug(f"Skipping projects that are not locally syncable in watch cycle: {skip}")

        return list(projects)

    async def run(self):  # pragma: no cover
        """Watch for file changes and sync them"""

        self.state.running = True
        self.state.start_time = datetime.now()
        await self.write_status()

        logger.info(
            "Watch service started",
            f"debounce_ms={self.app_config.sync_delay}",
            f"pid={os.getpid()}",
        )

        try:
            while self.state.running:
                # Clear ignore patterns cache to pick up any .gitignore changes
                self._ignore_patterns_cache.clear()

                projects = await self._select_projects_to_watch()

                # Trigger: no projects selected (e.g. constrained_project names a
                #          project not in the DB, or every project was filtered out)
                # Why: watchfiles.awatch() requires at least one path. Calling it
                #      with an empty list raises ValueError, which the outer handler
                #      catches with a 5s sleep — producing a tight error-log loop.
                # Outcome: sleep the configured reload interval before retrying, so
                #          newly added projects get picked up on the next cycle.
                if not projects:
                    logger.warning(
                        "No projects to watch; sleeping before retry "
                        f"(constrained_project={self.constrained_project!r})"
                    )
                    await asyncio.sleep(self.app_config.watch_project_reload_interval)
                    continue

                project_paths = [project.path for project in projects]
                logger.debug(f"Starting watch cycle for directories: {project_paths}")

                # Create stop event for this watch cycle
                stop_event = asyncio.Event()

                # Schedule restart after configured interval to reload projects
                timer_task = asyncio.create_task(self._schedule_restart(stop_event))

                try:
                    await self._watch_projects_cycle(projects, stop_event)
                except Exception as e:
                    logger.exception("Watch service error during cycle", error=str(e))
                    self.state.record_error(str(e))
                    await self.write_status()
                    # Continue to next cycle instead of exiting
                    await asyncio.sleep(5)  # Brief pause before retry
                finally:
                    # Cancel timer task if it's still running
                    if not timer_task.done():
                        timer_task.cancel()
                        try:
                            await timer_task
                        except asyncio.CancelledError:
                            pass

        except Exception as e:
            logger.exception("Watch service error", error=str(e))
            self.state.record_error(str(e))
            await self.write_status()
            raise

        finally:
            logger.info(
                "Watch service stopped",
                f"runtime_seconds={int((datetime.now() - self.state.start_time).total_seconds())}",
            )

            self.state.running = False
            await self.write_status()

    def filter_changes(self, change: Change, path: str) -> bool:
        """Filter to only watch non-hidden files and directories.

        Returns:
            True if the file should be watched, False if it should be ignored
        """

        project_paths = self._sorted_watch_filter_roots
        if project_paths is None:
            project_paths = local_watch_filter_roots(
                (entry for entry in self.app_config.projects.values() if entry.path)
            )

        return local_watch_path_is_observable(project_roots=project_paths, path=path)

    async def write_status(self):
        """Write current state to status file"""
        self.status_path.write_text(WatchServiceState.model_dump_json(self.state, indent=2))

    def is_project_path(self, project: Project, path):
        """
        Checks if path is a subdirectory or file within a project
        """
        return local_watch_path_is_under_project(
            project_root=local_project_root(project),
            path=path,
        )

    async def handle_changes(self, project: Project, changes: Set[FileChange]) -> None:
        """Process a batch of file changes"""
        # Check if project still exists in configuration before processing
        # This prevents deleted projects from being recreated by background sync
        from basic_memory.config import ConfigManager

        config_manager = ConfigManager()
        if (
            project.name not in config_manager.projects
            and project.permalink not in config_manager.projects
        ):
            logger.info(
                f"Skipping sync for deleted project: {project.name}, change_count={len(changes)}"
            )
            return

        if not changes:
            self.state.last_scan = datetime.now()
            await self.write_status()
            return

        if self._event_index_runtime_factory is not None:
            await self._handle_changes_with_event_index(project, changes)
            return

        sync_service = await self._get_sync_service(project)
        file_service = sync_service.file_service

        start_time = time.time()
        directory = Path(project.path).resolve()
        logger.info(
            f"Processing project: {project.name} changes, change_count={len(changes)}, directory={directory}"
        )

        # Group changes by type
        adds: List[str] = []
        deletes: List[str] = []
        modifies: List[str] = []

        for change, path in changes:
            # convert to relative path
            relative_path = Path(path).relative_to(directory).as_posix()

            # Skip .tmp files - they're temporary and shouldn't be synced
            if relative_path.endswith(".tmp"):
                continue

            if change == Change.added:
                adds.append(relative_path)
            elif change == Change.deleted:
                deletes.append(relative_path)
            elif change == Change.modified:
                modifies.append(relative_path)

        logger.debug(
            f"Grouped file changes, added={len(adds)}, deleted={len(deletes)}, modified={len(modifies)}"
        )

        # because of our atomic writes on updates, an add may be an existing file
        # Avoid mutating `adds` while iterating (can skip items).
        reclassified_as_modified: List[str] = []
        for added_path in list(adds):  # pragma: no cover TODO add test
            async with sync_service._session_scope() as session:
                entity = await sync_service.entity_repository.get_by_file_path(session, added_path)
            if entity is not None:
                logger.debug(f"Existing file will be processed as modified, path={added_path}")
                reclassified_as_modified.append(added_path)

        if reclassified_as_modified:
            adds = [p for p in adds if p not in reclassified_as_modified]
            modifies.extend(reclassified_as_modified)

        # Track processed files to avoid duplicates
        processed: Set[str] = set()

        # First handle potential moves
        for added_path in adds:
            if added_path in processed:
                continue  # pragma: no cover

            # Skip directories for added paths
            # We don't need to process directories, only the files inside them
            # This prevents errors when trying to compute checksums or read directories as files
            added_full_path = directory / added_path
            if not added_full_path.exists() or added_full_path.is_dir():
                logger.debug("Skipping non-existent or directory path", path=added_path)
                processed.add(added_path)
                continue

            for deleted_path in deletes:
                if deleted_path in processed:
                    continue  # pragma: no cover

                # Skip directories for deleted paths (based on entity type in db)
                async with sync_service._session_scope() as session:
                    deleted_entity = await sync_service.entity_repository.get_by_file_path(
                        session, deleted_path
                    )
                if deleted_entity is None:
                    # If this was a directory, it wouldn't have an entity
                    logger.debug("Skipping unknown path for move detection", path=deleted_path)
                    continue

                if added_path != deleted_path:
                    # Compare checksums to detect moves
                    try:
                        added_checksum = await file_service.compute_checksum(added_path)

                        if deleted_entity and deleted_entity.checksum == added_checksum:
                            await sync_service.handle_move(deleted_path, added_path)
                            self.state.add_event(
                                path=f"{deleted_path} -> {added_path}",
                                action="moved",
                                status="success",
                            )
                            self.console.print(f"[blue]→[/blue] {deleted_path} → {added_path}")
                            logger.info(f"move: {deleted_path} -> {added_path}")
                            processed.add(added_path)
                            processed.add(deleted_path)
                            break
                    except Exception as e:  # pragma: no cover
                        logger.warning(
                            "Error checking for move",
                            f"old_path={deleted_path}",
                            f"new_path={added_path}",
                            f"error={str(e)}",
                        )

        # Handle remaining changes - group them by type for concise output
        moved_count = len([p for p in processed if p in deletes or p in adds])
        delete_count = 0
        add_count = 0
        modify_count = 0

        # Process deletes
        for path in deletes:
            if path not in processed:
                # Check if file still exists on disk (vim atomic write edge case)
                full_path = directory / path
                if full_path.exists() and full_path.is_file():
                    # File still exists despite DELETE event - treat as modification
                    logger.debug(
                        "File exists despite DELETE event, treating as modification", path=path
                    )
                    entity, checksum = await sync_service.sync_file(path, new=False)
                    self.state.add_event(
                        path=path, action="modified", status="success", checksum=checksum
                    )
                    self.console.print(f"[yellow]✎[/yellow] {path} (atomic write)")
                    logger.info(f"atomic write detected: {path}")
                    processed.add(path)
                    modify_count += 1
                else:
                    # Check if this was a directory - skip if so
                    # (we can't tell if the deleted path was a directory since it no longer exists,
                    # so we check if there's an entity in the database for it)
                    async with sync_service._session_scope() as session:
                        entity = await sync_service.entity_repository.get_by_file_path(
                            session, path
                        )
                    if entity is None:
                        # No entity means this was likely a directory - skip it
                        logger.debug(
                            f"Skipping deleted path with no entity (likely directory), path={path}"
                        )
                        processed.add(path)
                        continue

                    # File truly deleted
                    logger.debug("Processing deleted file", path=path)
                    await sync_service.handle_delete(path)
                    self.state.add_event(path=path, action="deleted", status="success")
                    self.console.print(f"[red]✕[/red] {path}")
                    logger.info(f"deleted: {path}")
                    processed.add(path)
                    delete_count += 1

        # Process adds
        for path in adds:
            if path not in processed:
                # Skip directories - only process files
                full_path = directory / path
                if not full_path.exists() or full_path.is_dir():
                    logger.debug(
                        f"Skipping non-existent or directory path, path={path}"
                    )  # pragma: no cover
                    processed.add(path)  # pragma: no cover
                    continue  # pragma: no cover

                logger.debug(f"Processing new file, path={path}")
                entity, checksum = await sync_service.sync_file(path, new=True)
                if checksum:
                    self.state.add_event(
                        path=path, action="new", status="success", checksum=checksum
                    )
                    self.console.print(f"[green]✓[/green] {path}")
                    logger.info(
                        "new file processed",
                        f"path={path}",
                        f"checksum={checksum}",
                    )
                    processed.add(path)
                    add_count += 1
                else:  # pragma: no cover
                    logger.warning(f"Error syncing new file, path={path}")  # pragma: no cover
                    self.console.print(
                        f"[orange]?[/orange] Error syncing: {path}"
                    )  # pragma: no cover

        # Process modifies - detect repeats
        last_modified_path = None
        repeat_count = 0

        for path in modifies:
            if path not in processed:
                # Skip directories - only process files
                full_path = directory / path
                if not full_path.exists() or full_path.is_dir():
                    logger.debug("Skipping non-existent or directory path", path=path)
                    processed.add(path)
                    continue

                logger.debug(f"Processing modified file: path={path}")
                entity, checksum = await sync_service.sync_file(path, new=False)
                self.state.add_event(
                    path=path, action="modified", status="success", checksum=checksum
                )

                # Check if this is a repeat of the last modified file
                if path == last_modified_path:  # pragma: no cover
                    repeat_count += 1  # pragma: no cover
                    # Only show a message for the first repeat
                    if repeat_count == 1:  # pragma: no cover
                        self.console.print(
                            f"[yellow]...[/yellow] Repeated changes to {path}"
                        )  # pragma: no cover
                else:
                    # haven't processed this file
                    self.console.print(f"[yellow]✎[/yellow] {path}")
                    logger.info(f"modified: {path}")
                    last_modified_path = path
                    repeat_count = 0
                    modify_count += 1

                logger.debug(  # pragma: no cover
                    "Modified file processed, "
                    f"path={path} "
                    f"entity_id={entity.id if entity else None} "
                    f"checksum={checksum}",
                )
                processed.add(path)

        # Add a concise summary instead of a divider
        if processed:
            change_summary: list[str] = []
            if add_count > 0:
                change_summary.append(f"[green]{add_count} added[/green]")
            if modify_count > 0:
                change_summary.append(f"[yellow]{modify_count} modified[/yellow]")
            if moved_count > 0:
                change_summary.append(f"[blue]{moved_count} moved[/blue]")
            if delete_count > 0:
                change_summary.append(f"[red]{delete_count} deleted[/red]")

            if change_summary:
                self.console.print(f"{', '.join(change_summary)}", style="dim")
                logger.info(f"changes: {len(change_summary)}")

        duration_ms = int((time.time() - start_time) * 1000)
        self.state.last_scan = datetime.now()
        self.state.synced_files += len(processed)

        logger.info(
            "File change processing completed, "
            f"processed_files={len(processed)}, "
            f"total_synced_files={self.state.synced_files}, "
            f"duration_ms={duration_ms}"
        )

        await self.write_status()

    async def _handle_changes_with_event_index(
        self,
        project: Project,
        changes: Set[FileChange],
    ) -> None:
        """Process a project change batch through the opt-in event-index runtime."""
        if self._event_index_runtime_factory is None:
            raise RuntimeError("event-index change handling requires a runtime factory")

        start_time = time.time()
        directory = Path(project.path).expanduser().resolve()
        request = LocalWatchEventIndexRequest.from_project_changes(
            project=project,
            changes=changes,
            ignore_patterns=self._get_ignore_patterns(directory),
        )
        result = await run_local_watch_event_indexing(
            request,
            runtime=await self._event_index_runtime_factory.runtime_for_project(project),
        )

        self.state.last_scan = datetime.now()
        status_update = plan_local_watch_event_index_status_update(
            project_prefix=request.project_prefix,
            result=result,
        )
        self.state.synced_files += status_update.synced_files_increment
        if status_update.record_last_error:
            self.state.error_count += status_update.error_count_increment
            self.state.last_error = datetime.now()
        self.state.add_event(
            path=status_update.path,
            action=status_update.action,
            status=status_update.status,
            error=status_update.error,
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "Event-index file change processing completed, "
            f"processed_files={result.counts.processed}, "
            f"failed_files={result.counts.failed}, "
            f"skipped_files={result.counts.skipped}, "
            f"total_synced_files={self.state.synced_files}, "
            f"duration_ms={duration_ms}"
        )
        await self.write_status()
