"""Local filesystem watcher for event-based indexing."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
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
from basic_memory.config import BasicMemoryConfig, WATCH_STATUS_JSON
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
from basic_memory.models import Project
from basic_memory.repository import ProjectRepository


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

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[AsyncSession]:
        """Open a service-owned transaction."""
        async with db.scoped_session(self.session_maker) as session:
            yield session

    async def _schedule_restart(self, stop_event: asyncio.Event) -> None:
        """Schedule a watch cycle restart so project config changes are observed."""
        await asyncio.sleep(self.app_config.watch_project_reload_interval)
        stop_event.set()

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
                    self.handle_changes(batch.project, set(batch.changes))
                    for batch in project_changes
                ]
                await asyncio.gather(*change_handlers)
        finally:
            self._sorted_watch_filter_roots = previous_filter_roots

    async def _select_projects_to_watch(self) -> list[Project]:
        """Return locally syncable projects that this watcher instance owns."""
        async with self._session_scope() as session:
            projects = await self.project_repository.get_active_projects(session)

        if self.constrained_project:
            projects = [project for project in projects if project.name == self.constrained_project]

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
                timer_task = asyncio.create_task(self._schedule_restart(stop_event))

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
        """Return whether a project still exists in local config."""
        return (
            project.name in self.app_config.projects
            or project.permalink in self.app_config.projects
        )

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
            f"processed_files={result.counts.processed}, "
            f"failed_files={result.counts.failed}, "
            f"skipped_files={result.counts.skipped}, "
            f"total_indexed_files={self.state.indexed_files}, "
            f"duration_ms={duration_ms}"
        )
        await self.write_status()
