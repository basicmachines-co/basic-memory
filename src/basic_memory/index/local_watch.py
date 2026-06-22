"""Local watcher orchestration for event-based indexing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from watchfiles.main import FileChange

from basic_memory.ignore_utils import should_ignore_path
from basic_memory.index.filesystem import (
    LOCAL_FILESYSTEM_BUCKET_NAME,
    LocalFilesystemIgnorePatterns,
    local_storage_events_from_watchfiles_changes,
)
from basic_memory.index.local_moves import LocalWatchMoveProcessor
from basic_memory.index.storage_events import (
    StorageEventIndexRuntime,
    run_storage_event_indexing,
)
from basic_memory.runtime import (
    ProjectPath,
    RuntimeStorageEventProcessingResult,
    StorageBucketName,
    StorageEventPayload,
    group_storage_events_by_bucket,
)

LocalWatchProjectT = TypeVar("LocalWatchProjectT", bound="LocalWatchProjectSource")


class LocalWatchProjectSource(Protocol):
    """Minimal project shape needed to build local watcher storage events."""

    @property
    def path(self) -> object: ...


def local_project_root(project: LocalWatchProjectSource) -> Path:
    """Return the resolved filesystem root for a local watcher project."""
    project_path = str(project.path).strip() if project.path else ""
    if not project_path:
        raise ValueError("local watcher project requires path")
    return Path(project_path).expanduser().resolve()


def local_project_prefix(project: LocalWatchProjectSource) -> ProjectPath:
    """Return the storage-event prefix for a local watcher project."""
    return local_project_root(project).name


def local_watch_filter_roots(projects: Iterable[LocalWatchProjectSource]) -> tuple[Path, ...]:
    """Return watcher roots ordered for project-relative hidden-path checks."""
    return tuple(
        sorted(
            (local_project_root(project) for project in projects),
            key=lambda project_root: len(project_root.parts),
        )
    )


def local_watch_path_is_observable(*, project_roots: Iterable[Path], path: Path | str) -> bool:
    """Return whether a filesystem watcher path should reach local indexing."""
    path_obj = Path(path).expanduser().resolve()

    relative_path = None
    for project_root in project_roots:
        try:
            relative_path = path_obj.relative_to(project_root)
            break
        except ValueError:
            continue

    path_parts = relative_path.parts if relative_path is not None else path_obj.parts
    if any(path_part.startswith(".") for path_part in path_parts):
        return False

    return not path_obj.name.endswith(".tmp")


def local_watch_path_is_under_project(*, project_root: Path, path: Path | str) -> bool:
    """Return whether path is a descendant of a local project root."""
    project_root = project_root.expanduser().resolve()
    path_obj = Path(path).expanduser().resolve()
    if path_obj == project_root:
        return False
    try:
        path_obj.relative_to(project_root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class LocalWatchProjectChangeBatch(Generic[LocalWatchProjectT]):
    """A watcher change batch routed to one local project."""

    project: LocalWatchProjectT
    changes: tuple[FileChange, ...]


def local_watch_project_change_batches(
    *,
    projects: Iterable[LocalWatchProjectT],
    changes: Iterable[FileChange],
    ignore_patterns_by_project_root: Mapping[Path, LocalFilesystemIgnorePatterns],
) -> tuple[LocalWatchProjectChangeBatch[LocalWatchProjectT], ...]:
    """Route watcher changes to project-scoped batches before indexing."""
    project_roots = tuple((project, local_project_root(project)) for project in projects)
    routed_changes: list[tuple[LocalWatchProjectT, list[FileChange]]] = [
        (project, []) for project, _project_root in project_roots
    ]

    for change, path in changes:
        file_path = Path(path).expanduser().resolve()
        for index, (_project, project_root) in enumerate(project_roots):
            if not local_watch_path_is_under_project(project_root=project_root, path=file_path):
                continue

            ignore_patterns = ignore_patterns_by_project_root[project_root]
            if should_ignore_path(file_path, project_root, ignore_patterns):
                continue

            routed_changes[index][1].append((change, path))
            break

    return tuple(
        LocalWatchProjectChangeBatch(project=project, changes=tuple(project_changes))
        for project, project_changes in routed_changes
        if project_changes
    )


@dataclass(frozen=True, slots=True)
class LocalWatchEventIndexRequest:
    """One local watcher change batch ready for event-index orchestration."""

    project_root: Path
    project_prefix: ProjectPath
    changes: tuple[FileChange, ...]
    event_time: str | None = None
    bucket_name: StorageBucketName = LOCAL_FILESYSTEM_BUCKET_NAME
    ignore_patterns: LocalFilesystemIgnorePatterns | None = None

    @classmethod
    def from_changes(
        cls,
        *,
        project_root: Path,
        project_prefix: ProjectPath,
        changes: Iterable[FileChange],
        event_time: str | None = None,
        bucket_name: StorageBucketName = LOCAL_FILESYSTEM_BUCKET_NAME,
        ignore_patterns: LocalFilesystemIgnorePatterns | None = None,
    ) -> "LocalWatchEventIndexRequest":
        """Build a stable request from a watchfiles change iterable."""
        return cls(
            project_root=project_root,
            project_prefix=project_prefix,
            changes=tuple(changes),
            event_time=event_time,
            bucket_name=bucket_name,
            ignore_patterns=ignore_patterns,
        )

    @classmethod
    def from_project_changes(
        cls,
        *,
        project: LocalWatchProjectSource,
        changes: Iterable[FileChange],
        event_time: str | None = None,
        bucket_name: StorageBucketName = LOCAL_FILESYSTEM_BUCKET_NAME,
        ignore_patterns: LocalFilesystemIgnorePatterns | None = None,
    ) -> "LocalWatchEventIndexRequest":
        """Build a watcher request from the configured local project."""
        project_root = local_project_root(project)
        return cls.from_changes(
            project_root=project_root,
            project_prefix=local_project_prefix(project),
            changes=changes,
            event_time=event_time,
            bucket_name=bucket_name,
            ignore_patterns=ignore_patterns,
        )


@dataclass(frozen=True, slots=True)
class LocalWatchEventIndexStatusUpdate:
    """Watch-status mutation planned from a local event-index result."""

    path: ProjectPath
    status: str
    indexed_files_increment: int
    error_count_increment: int
    error: str | None = None
    action: str = "index"

    @property
    def record_last_error(self) -> bool:
        return self.error_count_increment > 0


def plan_local_watch_event_index_status_update(
    *,
    project_prefix: ProjectPath,
    result: RuntimeStorageEventProcessingResult,
) -> LocalWatchEventIndexStatusUpdate:
    """Plan the local watch-status update for an event-index result."""
    if result.counts.failed:
        return LocalWatchEventIndexStatusUpdate(
            path=project_prefix,
            status="error",
            indexed_files_increment=result.counts.processed,
            error_count_increment=result.counts.failed,
            error=(
                f"event-index processed={result.counts.processed} "
                f"failed={result.counts.failed} skipped={result.counts.skipped}"
            ),
        )

    return LocalWatchEventIndexStatusUpdate(
        path=project_prefix,
        status="success",
        indexed_files_increment=result.counts.processed,
        error_count_increment=0,
    )


@dataclass(frozen=True, slots=True)
class LocalWatchStorageEventIndexRuntime(StorageEventIndexRuntime):
    """Storage-event runtime with local watcher move detection."""

    move_processor: LocalWatchMoveProcessor | None = None


@dataclass(frozen=True, slots=True)
class LocalWatchStorageEventSource:
    """Normalize local watcher changes into the shared storage-event source shape."""

    request: LocalWatchEventIndexRequest

    def events(self) -> tuple[StorageEventPayload, ...]:
        return local_storage_events_from_watchfiles_changes(
            project_root=self.request.project_root,
            project_prefix=self.request.project_prefix,
            changes=self.request.changes,
            event_time=self.request.event_time,
            bucket_name=self.request.bucket_name,
            ignore_patterns=self.request.ignore_patterns,
        )

    def events_by_bucket(self) -> dict[StorageBucketName, tuple[StorageEventPayload, ...]]:
        return group_storage_events_by_bucket(self.events())


async def run_local_watch_event_indexing(
    request: LocalWatchEventIndexRequest,
    *,
    runtime: StorageEventIndexRuntime,
) -> RuntimeStorageEventProcessingResult:
    """Normalize local file changes and process them through storage-event indexing."""
    event_source = LocalWatchStorageEventSource(request)
    events = event_source.events()
    result = RuntimeStorageEventProcessingResult.empty()
    if isinstance(runtime, LocalWatchStorageEventIndexRuntime):
        move_processor = runtime.move_processor
        if move_processor is not None:
            move_result = await move_processor.process_moves(events)
            events = move_result.remaining_events
            result = result.with_processed(move_result.processed_moves)

    return result.add(await run_storage_event_indexing(events, runtime))
