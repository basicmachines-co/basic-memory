"""Local filesystem adapters for event-based indexing."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from watchfiles import Change
from watchfiles.main import FileChange

from basic_memory.ignore_utils import should_ignore_path
from basic_memory.runtime import (
    StorageBucketName,
    StorageEventInput,
    StorageEventPayload,
    StorageKey,
    storage_event_payload_from_input,
)
from basic_memory.runtime.contracts import STORAGE_OBJECT_DELETED_EVENT
from basic_memory.runtime.storage_project_resolution import storage_object_key_from_project_prefix

LOCAL_FILESYSTEM_BUCKET_NAME: StorageBucketName = "local-filesystem"
LOCAL_FILESYSTEM_CREATED_EVENT = "OBJECT_CREATED_PUT"
LOCAL_FILTERED_FILE_SUFFIXES = (".tmp", ".swp", "~")
type LocalFilesystemIgnorePatterns = set[str]


def local_storage_events_from_watchfiles_changes(
    *,
    project_root: Path,
    project_prefix: str,
    changes: Iterable[FileChange],
    event_time: str | None = None,
    bucket_name: StorageBucketName = LOCAL_FILESYSTEM_BUCKET_NAME,
    ignore_patterns: LocalFilesystemIgnorePatterns | None = None,
) -> tuple[StorageEventPayload, ...]:
    """Convert watchfiles changes into normalized storage-event payloads."""
    observed_at = event_time or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    project_root = project_root.expanduser().resolve()
    return tuple(
        storage_event_payload_from_input(event_input)
        for event_input in local_storage_event_inputs_from_watchfiles_changes(
            project_root=project_root,
            project_prefix=project_prefix,
            changes=changes,
            event_time=observed_at,
            bucket_name=bucket_name,
            ignore_patterns=ignore_patterns,
        )
    )


def local_storage_event_inputs_from_watchfiles_changes(
    *,
    project_root: Path,
    project_prefix: str,
    changes: Iterable[FileChange],
    event_time: str,
    bucket_name: StorageBucketName = LOCAL_FILESYSTEM_BUCKET_NAME,
    ignore_patterns: LocalFilesystemIgnorePatterns | None = None,
) -> tuple[StorageEventInput, ...]:
    """Convert watchfiles changes into runtime storage-event inputs."""
    project_root = project_root.expanduser().resolve()
    event_inputs: list[StorageEventInput] = []

    for change, path in changes:
        event_input = local_storage_event_input_from_watchfiles_change(
            project_root=project_root,
            project_prefix=project_prefix,
            change=change,
            path=Path(path),
            event_time=event_time,
            bucket_name=bucket_name,
            ignore_patterns=ignore_patterns,
        )
        if event_input is not None:
            event_inputs.append(event_input)

    event_inputs.sort(key=local_storage_event_input_order)
    return coalesce_local_storage_event_inputs(event_inputs)


def local_storage_event_input_from_watchfiles_change(
    *,
    project_root: Path,
    project_prefix: str,
    change: Change,
    path: Path,
    event_time: str,
    bucket_name: StorageBucketName = LOCAL_FILESYSTEM_BUCKET_NAME,
    ignore_patterns: LocalFilesystemIgnorePatterns | None = None,
) -> StorageEventInput | None:
    """Normalize one watchfiles change into a storage-event input."""
    path = path.expanduser().resolve()
    try:
        relative_path = path.relative_to(project_root).as_posix()
    except ValueError:
        return None
    if local_relative_path_is_filtered(relative_path):
        return None
    if ignore_patterns is not None and should_ignore_path(path, project_root, ignore_patterns):
        return None

    if path.exists() and path.is_dir():
        return None

    event_name = local_storage_event_name_for_change(change, path)
    if event_name is None:
        return None

    return StorageEventInput(
        event_name=event_name,
        event_time=event_time,
        bucket_name=bucket_name,
        object_key=local_storage_object_key(
            project_prefix=project_prefix,
            relative_path=relative_path,
        ),
        etag=local_storage_etag(path),
        size=local_storage_size(path),
    )


def local_relative_path_is_filtered(relative_path: str) -> bool:
    """Return whether a project-relative path should be ignored before indexing."""
    if relative_path.endswith(LOCAL_FILTERED_FILE_SUFFIXES):
        return True
    return any(path_part.startswith(".") for path_part in Path(relative_path).parts)


def local_storage_event_input_order(event_input: StorageEventInput) -> tuple[int, StorageKey]:
    """Order local batch deletes before creates so move batches clear stale entities first."""
    if event_input.event_name == STORAGE_OBJECT_DELETED_EVENT:
        return (0, event_input.object_key)
    return (1, event_input.object_key)


def coalesce_local_storage_event_inputs(
    event_inputs: Iterable[StorageEventInput],
) -> tuple[StorageEventInput, ...]:
    """Drop duplicate local watcher events that resolve to the same storage operation."""
    coalesced: list[StorageEventInput] = []
    observed: set[tuple[str, StorageKey]] = set()
    for event_input in event_inputs:
        event_key = (event_input.event_name, event_input.object_key)
        if event_key in observed:
            continue
        observed.add(event_key)
        coalesced.append(event_input)
    return tuple(coalesced)


def local_storage_event_name_for_change(change: Change, path: Path) -> str | None:
    """Map a filesystem watcher change into the portable storage event vocabulary."""
    if change in {Change.added, Change.modified}:
        return LOCAL_FILESYSTEM_CREATED_EVENT
    if change == Change.deleted:
        if path.exists() and path.is_file():
            return LOCAL_FILESYSTEM_CREATED_EVENT
        return STORAGE_OBJECT_DELETED_EVENT
    return None


def local_storage_object_key(*, project_prefix: str, relative_path: str) -> StorageKey:
    """Build a cloud-compatible object key for a local project-relative path."""
    return storage_object_key_from_project_prefix(project_prefix, relative_path)


def local_storage_etag(path: Path) -> str:
    """Return metadata identity for a local file event without reading file contents."""
    if not path.exists() or not path.is_file():
        return "missing"
    stat = path.stat()
    return f"local:{stat.st_mtime_ns}:{stat.st_size}"


def local_storage_size(path: Path) -> int | None:
    """Return the current file size for existing local files."""
    if not path.exists() or not path.is_file():
        return None
    return path.stat().st_size
