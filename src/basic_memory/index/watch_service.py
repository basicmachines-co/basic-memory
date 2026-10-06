"""Local filesystem watcher for event-based indexing."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from loguru import logger
from pydantic import BaseModel, Field
from rich.console import Console
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from watchfiles import awatch
from watchfiles.main import Change, FileChange

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager, WATCH_STATUS_JSON
from basic_memory.ignore_utils import load_gitignore_patterns
from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
from basic_memory.index.local_watch import (
    LocalWatchEventIndexRequest,
    local_project_root,
    local_watch_filter_roots,
    local_watch_path_is_observable,
    local_watch_path_is_under_project,
    local_watch_project_change_batches,
    plan_local_watch_event_index_status_update,
    run_local_watch_event_indexing,
)
from basic_memory.index.storage_events import StorageEventIndexRuntime
from basic_memory.index.watch_reconcile import (
    FileSignature,
    expand_deleted_directories,
    expand_new_directories,
    indexed_file_paths,
    settled_project_files,
)
from basic_memory.models import Project
from basic_memory.repository import ProjectRepository
from basic_memory.utils import generate_permalink


class WatchEvent(BaseModel):
    """One user-visible local watcher event."""

    timestamp: datetime
    path: str
    action: str
    status: str
    checksum: str | None = None
    error: str | None = None


class WatchServiceState(BaseModel):
    """Serializable local watcher status."""

    running: bool = False
    start_time: datetime = Field(default_factory=datetime.now)
    pid: int = Field(default_factory=os.getpid)
    error_count: int = 0
    last_error: datetime | None = None
    last_scan: datetime | None = None
    indexed_files: int = 0
    recent_events: list[WatchEvent] = Field(default_factory=list)

    def add_event(
        self,
        *,
        path: str,
        action: str,
        status: str,
        checksum: str | None = None,
        error: str | None = None,
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
        self.recent_events = self.recent_events[:100]
        return event

    def record_error(self, error: str) -> None:
        self.error_count += 1
        self.last_error = datetime.now()
        self.add_event(path="", action="index", status="error", error=error)


class WatchEventIndexRuntimeFactory(Protocol):
    """Build event-index runtime dependencies for one watched project."""

    async def runtime_for_project(self, project: Project) -> StorageEventIndexRuntime: ...


class WatchService:
    """Watch local project files and emit normalized storage events into indexing."""

    def __init__(
        self,
        app_config: BasicMemoryConfig,
        project_repository: ProjectRepository,
        session_maker: async_sessionmaker[AsyncSession],
        quiet: bool = False,
        event_index_runtime_factory: WatchEventIndexRuntimeFactory | None = None,
        constrained_project: str | None = None,
        initial_index_pending: Callable[[], bool] | None = None,
    ) -> None:
        self.app_config = app_config
        self.project_repository = project_repository
        self.session_maker = session_maker
        self.state = WatchServiceState()
        self.status_path = app_config.data_dir_path / WATCH_STATUS_JSON
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self._ignore_patterns_cache: dict[Path, set[str]] = {}
        self._sorted_watch_filter_roots: tuple[Path, ...] | None = None
        self._event_index_runtime_factory = (
            event_index_runtime_factory
            or LocalWatchEventIndexRuntimeFactory(
                index_embeddings=app_config.semantic_search_enabled,
            )
        )
        self.constrained_project = constrained_project
        self.console = Console(quiet=quiet)
        # State for finding files the watcher missed.
        # See _schedule_restart, expand_new_directories and reconcile_unindexed_files.
        self.reconcile_enabled = True
        self.reconcile_settle_seconds = 60.0
        self._initial_index_pending = initial_index_pending or (lambda: False)
        self._batches_in_flight = 0
        self._last_batch_finished = 0.0
        self._reconcile_attempted: dict[int, dict[str, FileSignature]] = {}
        self._new_directories_seen = False
        self._reconcile_after_restart = False

    async def _schedule_restart(
        self,
        stop_event: asyncio.Event,
        projects: Sequence[Project] = (),
    ) -> None:
        """Maintain one watch cycle: reconcile, re-check projects, restart when needed.

        This used to stop the cycle on every tick, to pick up project changes, and
        that restart was lossy twice over. Stopping discards the changes the watcher
        has collected but not yet handed over (watchfiles clears them when it sees
        the stop event), and the next cycle's fresh watcher never hears of files
        that already exist; while a slow batch was being indexed, a tick dropped
        everything written in the meantime. Nothing ever looked for what had been
        dropped.

        Now a tick restarts only when there is a reason -- the project set changed,
        or a directory appeared that the watcher may not be watching (see
        expand_new_directories) -- and every restart is followed, once indexing is
        quiet, by a reconcile that indexes whatever the gap cost. A tick with no
        reason to restart runs the reconcile on its own: the periodic safety net.
        """
        watched = _watched_project_identity(projects)
        if self._reconcile_after_restart and self.reconcile_enabled:
            await asyncio.sleep(self._quiet_seconds())
            if await self._reconcile_when_idle(projects):
                self._reconcile_after_restart = False
        while not stop_event.is_set():
            await asyncio.sleep(self.app_config.watch_project_reload_interval)
            try:
                current = await self._select_projects_to_watch()
            except Exception as exc:
                # Trigger: the project list could not be read (database unavailable).
                # Why: carrying on would leave a stale project set watched forever.
                # Outcome: restart the cycle, which retries the read, as upstream did.
                logger.warning(f"Watch project reload failed, restarting watch cycle: {exc}")
                self._restart(stop_event)
                return
            if self._project_set_changed(watched, current):
                logger.info("Watched project set changed; restarting watch cycle")
                self._restart(stop_event)
                return
            if self._new_directories_seen:
                logger.info(
                    "New directories appeared since the watcher started; restarting the "
                    "watch cycle so that every one of them is watched"
                )
                self._restart(stop_event)
                return
            # Pick up .gitignore/.bmignore edits, which the restart used to do.
            self._ignore_patterns_cache.clear()
            if self.reconcile_enabled and await self._reconcile_when_idle(current):
                self._reconcile_after_restart = False

    def _restart(self, stop_event: asyncio.Event) -> None:
        """End this watch cycle; the next one reconciles what the gap cost."""
        self._reconcile_after_restart = True
        stop_event.set()

    def _quiet_seconds(self) -> float:
        """How long indexing must have been idle before a reconcile may run."""
        return 3 * self.app_config.index_delay / 1000 + 5

    def _project_set_changed(
        self,
        watched: frozenset[tuple[str, str]],
        current: Sequence[Project],
    ) -> bool:
        """Return whether the projects to watch differ from the ones being watched."""
        return _watched_project_identity(current) != watched

    async def _reconcile_when_idle(self, projects: Sequence[Project]) -> bool:
        """Run the reconcile unless indexing is busy right now. Return whether it ran.

        A batch in flight, or one that just finished, can leave changes queued in
        the watcher that it will deliver in a moment; reconciling then would index
        them twice and report them as missed. The startup scan has the same view
        of a cold project. The next tick tries again.
        """
        if (
            self._batches_in_flight
            or self._initial_index_pending()
            or time.monotonic() - self._last_batch_finished < self._quiet_seconds()
        ):
            logger.debug("Skipping index reconcile: indexing is busy")
            return False
        try:
            await self.reconcile_unindexed_files(projects)
        except Exception as exc:
            logger.exception(f"Index reconcile failed: {exc}")
            self.state.record_error(str(exc))
            await self.write_status()
        return True

    async def reconcile_unindexed_files(self, projects: Sequence[Project]) -> int:
        """Index every settled file on disk that the index does not know. Return the count.

        The safety net under the watcher. Whatever made
        the watcher miss a file, this finds it within one reload interval instead
        of at the next restart, and says so in the log, because a reconcile that
        has to index something means an event was lost and that is worth knowing.

        A file it already tried, which is still not in the index and has not
        changed since, is not retried or reported again on every tick.
        """
        reconciled = 0
        for project in projects:
            if not self._project_is_configured(project):
                continue
            project_root = local_project_root(project)
            on_disk = await asyncio.to_thread(
                settled_project_files,
                project_root,
                ignore_patterns=self._get_ignore_patterns(project_root),
                settle_seconds=self.reconcile_settle_seconds,
            )
            indexed = await indexed_file_paths(self.session_maker, project.id)
            unindexed = {
                path: signature for path, signature in on_disk.items() if path not in indexed
            }
            already_tried = self._reconcile_attempted.get(project.id, {})
            to_index = sorted(
                path
                for path, signature in unindexed.items()
                if already_tried.get(path) != signature
            )
            self._reconcile_attempted[project.id] = unindexed
            if not to_index:
                continue

            logger.warning(
                f"Index reconcile: {len(to_index)} file(s) in project {project.name} were "
                "on disk but not in the index, so the watcher missed them; indexing now",
                project=project.name,
                file_count=len(to_index),
                paths=to_index[:20],
            )
            await self._handle_changes_isolated(
                project,
                {(Change.added, str(project_root / path)) for path in to_index},
            )
            reconciled += len(to_index)
        return reconciled

    def _get_ignore_patterns(self, project_path: Path) -> set[str]:
        """Return cached ignore patterns for one project root."""
        if project_path not in self._ignore_patterns_cache:
            self._ignore_patterns_cache[project_path] = load_gitignore_patterns(project_path)
        return self._ignore_patterns_cache[project_path]

    async def _watch_projects_cycle(
        self,
        projects: Sequence[Project],
        stop_event: asyncio.Event,
    ) -> None:
        """Run one watchfiles cycle and route batches into project-local indexing."""
        project_paths = [project.path for project in projects]
        previous_filter_roots = self._sorted_watch_filter_roots
        # A fresh watcher walks the tree and watches all of it.
        self._new_directories_seen = False
        self._sorted_watch_filter_roots = local_watch_filter_roots(projects)

        try:
            async for changes in awatch(
                *project_paths,
                debounce=self.app_config.index_delay,
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
                change_handlers = [
                    self._handle_changes_isolated(batch.project, set(batch.changes))
                    for batch in project_changes
                ]
                await asyncio.gather(*change_handlers)
        finally:
            self._sorted_watch_filter_roots = previous_filter_roots

    async def _select_projects_to_watch(self) -> list[Project]:
        """Return locally syncable projects that this watcher instance owns."""
        async with db.scoped_session(self.session_maker) as session:
            projects = await self.project_repository.get_active_projects(session)

        if self.constrained_project:
            constrained_permalink = generate_permalink(self.constrained_project)
            projects = [
                project for project in projects if project.permalink == constrained_permalink
            ]

        skipped = [
            project.name
            for project in projects
            if not self.app_config.is_locally_syncable(project.name, project.path)
        ]
        if skipped:
            projects = [project for project in projects if project.name not in skipped]
            logger.debug(f"Skipping projects that are not locally syncable: {skipped}")

        return list(projects)

    async def run(self) -> None:  # pragma: no cover
        """Run the local event-index watcher until stopped."""
        self.state.running = True
        self.state.start_time = datetime.now()
        await self.write_status()

        logger.info(
            "Event-index watch service started",
            f"debounce_ms={self.app_config.index_delay}",
            f"pid={os.getpid()}",
        )

        try:
            while self.state.running:
                self._ignore_patterns_cache.clear()
                projects = await self._select_projects_to_watch()

                if not projects:
                    logger.warning(
                        "No projects to watch; sleeping before retry "
                        f"(constrained_project={self.constrained_project!r})"
                    )
                    await asyncio.sleep(self.app_config.watch_project_reload_interval)
                    continue

                logger.debug(
                    f"Starting event-index watch cycle for directories: "
                    f"{[project.path for project in projects]}"
                )
                stop_event = asyncio.Event()
                timer_task = asyncio.create_task(self._schedule_restart(stop_event, projects))

                try:
                    await self._watch_projects_cycle(projects, stop_event)
                except Exception as exc:
                    logger.exception("Event-index watch service error during cycle", error=str(exc))
                    self.state.record_error(str(exc))
                    await self.write_status()
                    await asyncio.sleep(5)
                finally:
                    if not timer_task.done():
                        timer_task.cancel()
                        try:
                            await timer_task
                        except asyncio.CancelledError:
                            pass

        except Exception as exc:
            logger.exception("Event-index watch service error", error=str(exc))
            self.state.record_error(str(exc))
            await self.write_status()
            raise

        finally:
            logger.info(
                "Event-index watch service stopped",
                f"runtime_seconds={int((datetime.now() - self.state.start_time).total_seconds())}",
            )
            self.state.running = False
            await self.write_status()

    def filter_changes(self, change: Change, path: str) -> bool:
        """Return whether a watchfiles path should become a storage event."""
        project_roots = self._sorted_watch_filter_roots
        if project_roots is None:
            project_roots = local_watch_filter_roots(
                entry for entry in self.app_config.projects.values() if entry.path
            )
        return local_watch_path_is_observable(project_roots=project_roots, path=path)

    async def write_status(self) -> None:
        """Persist current watcher state for status endpoints."""
        self.status_path.write_text(self.state.model_dump_json(indent=2))

    def is_project_path(self, project: Project, path: Path | str) -> bool:
        """Return whether a path belongs under a watched project root."""
        return local_watch_path_is_under_project(
            project_root=local_project_root(project),
            path=path,
        )

    def _project_is_configured(self, project: Project) -> bool:
        """Return whether a project still exists in local config.

        Re-reads current config (via ConfigManager's mtime-validated cache) rather
        than the startup snapshot captured in self.app_config: a project deleted
        after the watcher started must not be re-indexed by background sync.
        """
        current_projects = ConfigManager().config.projects
        return project.name in current_projects or project.permalink in current_projects

    async def _handle_changes_isolated(self, project: Project, changes: set[FileChange]) -> None:
        """Process one project's batch, containing failures to that project.

        Trigger: handle_changes raised (move maintenance, runtime build, indexing).
        Why: change_handlers are awaited with asyncio.gather, which would otherwise
             propagate a single project's error and drop every project's batch for
             this watch cycle.
        Outcome: log + record the error and continue so other projects still index.
        """
        self._batches_in_flight += 1  # the reconcile waits for idle
        try:
            await self.handle_changes(project, changes)
        except Exception as exc:
            logger.exception(
                f"Event-index batch failed for project {project.name}: {exc}",
            )
            self.state.record_error(str(exc))
            await self.write_status()
        finally:
            self._batches_in_flight -= 1
            self._last_batch_finished = time.monotonic()

    async def handle_changes(self, project: Project, changes: set[FileChange]) -> None:
        """Normalize one project's watchfiles batch and process it through indexing."""
        if not self._project_is_configured(project):
            logger.info(
                f"Skipping event-index batch for deleted project: "
                f"{project.name}, change_count={len(changes)}"
            )
            return

        if not changes:
            self.state.last_scan = datetime.now()
            await self.write_status()
            return

        start_time = time.time()
        project_root = local_project_root(project)
        # A new directory's files may never be reported; see expand_new_directories.
        changes, new_directories = await asyncio.to_thread(
            expand_new_directories,
            changes,
            project_root=project_root,
            ignore_patterns=self._get_ignore_patterns(project_root),
        )
        if new_directories:
            self._new_directories_seen = True
            if any(change == Change.deleted for change, _path in changes):
                changes = expand_deleted_directories(
                    changes,
                    project_root=project_root,
                    indexed_paths=await indexed_file_paths(self.session_maker, project.id),
                )
        request = LocalWatchEventIndexRequest.from_project_changes(
            project=project,
            changes=changes,
            ignore_patterns=self._get_ignore_patterns(project_root),
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
        self.state.indexed_files += status_update.indexed_files_increment
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
            f"processed_files={result.processed}, "
            f"failed_files={result.failed}, "
            f"skipped_files={result.skipped}, "
            f"total_indexed_files={self.state.indexed_files}, "
            f"duration_ms={duration_ms}"
        )
        await self.write_status()


def _watched_project_identity(projects: Sequence[Project]) -> frozenset[tuple[str, str]]:
    """Name and path of each watched project: what a watch cycle depends on."""
    # A maintenance tick restarts the cycle only when this changes.
    return frozenset((str(project.name), str(project.path)) for project in projects)
