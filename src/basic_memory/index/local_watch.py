"""Local watcher orchestration for event-based indexing."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from watchfiles.main import FileChange

from basic_memory.index.filesystem import (
    LOCAL_FILESYSTEM_BUCKET_NAME,
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
)


@dataclass(frozen=True, slots=True)
class LocalWatchEventIndexRequest:
    """One local watcher change batch ready for event-index orchestration."""

    project_root: Path
    project_prefix: ProjectPath
    changes: tuple[FileChange, ...]
    event_time: str | None = None
    bucket_name: StorageBucketName = LOCAL_FILESYSTEM_BUCKET_NAME

    @classmethod
    def from_changes(
        cls,
        *,
        project_root: Path,
        project_prefix: ProjectPath,
        changes: Iterable[FileChange],
        event_time: str | None = None,
        bucket_name: StorageBucketName = LOCAL_FILESYSTEM_BUCKET_NAME,
    ) -> "LocalWatchEventIndexRequest":
        """Build a stable request from a watchfiles change iterable."""
        return cls(
            project_root=project_root,
            project_prefix=project_prefix,
            changes=tuple(changes),
            event_time=event_time,
            bucket_name=bucket_name,
        )


@dataclass(frozen=True, slots=True)
class LocalWatchStorageEventIndexRuntime(StorageEventIndexRuntime):
    """Storage-event runtime with local watcher move detection."""

    move_processor: LocalWatchMoveProcessor | None = None


async def run_local_watch_event_indexing(
    request: LocalWatchEventIndexRequest,
    *,
    runtime: StorageEventIndexRuntime,
) -> RuntimeStorageEventProcessingResult:
    """Normalize local file changes and process them through storage-event indexing."""
    events = local_storage_events_from_watchfiles_changes(
        project_root=request.project_root,
        project_prefix=request.project_prefix,
        changes=request.changes,
        event_time=request.event_time,
        bucket_name=request.bucket_name,
    )
    result = RuntimeStorageEventProcessingResult.empty()
    if isinstance(runtime, LocalWatchStorageEventIndexRuntime):
        move_processor = runtime.move_processor
        if move_processor is not None:
            move_result = await move_processor.process_moves(events)
            events = move_result.remaining_events
            result = result.with_processed(move_result.processed_moves)

    return result.add(await run_storage_event_indexing(events, runtime))
