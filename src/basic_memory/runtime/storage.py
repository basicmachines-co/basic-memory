"""Portable storage-object and storage-event contracts for Basic Memory runtimes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from basic_memory.runtime.projects import ProjectRuntimeReference

type ProjectId = int
type ProjectExternalId = str
type ProjectName = str
type ProjectPath = str
type ProjectPermalink = str
type RuntimeEntityId = int
type RuntimeContentType = str
type RuntimeIntegrityErrorMessage = str
type RuntimeFilePath = str
type StorageBucketName = str
type StorageKey = str
type StorageEtag = str
type StorageEventName = str
type StorageVersionId = str
type NoteExternalId = str
type RuntimeFileChecksum = str
type RuntimeNoteContentVersion = int
type RuntimeNoteContentVersionInput = RuntimeNoteContentVersion | str
type RuntimeNoteContentChecksum = str
type RuntimeNoteActorKind = str
type RuntimeNoteActorName = str
type RuntimeNoteChangeSource = str

STORAGE_OBJECT_CREATED_EVENTS: frozenset[StorageEventName] = frozenset(
    {"OBJECT_CREATED_PUT", "OBJECT_CREATED_POST"}
)
STORAGE_OBJECT_DELETED_EVENT: StorageEventName = "OBJECT_DELETED"
RUNTIME_MARKDOWN_CONTENT_TYPE: RuntimeContentType = "text/markdown"
RUNTIME_MARKDOWN_FILE_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown"})


def runtime_file_path_is_markdown_note(relative_path: RuntimeFilePath) -> bool:
    """Return whether a runtime file path is eligible for markdown-note indexing."""
    return PurePosixPath(relative_path).suffix.lower() in RUNTIME_MARKDOWN_FILE_SUFFIXES


class RuntimeContentTypeSource(Protocol):
    """Minimal source shape for runtime content-type decisions."""

    @property
    def content_type(self) -> RuntimeContentType: ...


def runtime_content_type_is_markdown(source: RuntimeContentTypeSource) -> bool:
    """Return whether a runtime source represents a markdown note."""
    return source.content_type == RUNTIME_MARKDOWN_CONTENT_TYPE


class StorageEventSource(Protocol):
    """Capability for reading normalized storage events from an ingress payload."""

    def events_by_bucket(self) -> Mapping[StorageBucketName, tuple[StorageEventPayload, ...]]: ...


@dataclass(frozen=True, slots=True)
class StorageObjectIdentity:
    """A storage object key with helpers for project-prefixed storage."""

    bucket_name: StorageBucketName
    key: StorageKey

    @property
    def project_path(self) -> ProjectPath:
        parts = self.key.split("/", 1)
        return parts[0] if len(parts) == 2 else ""

    @property
    def relative_path(self) -> str:
        parts = self.key.split("/", 1)
        return parts[1] if len(parts) == 2 else ""


@dataclass(frozen=True, slots=True)
class StorageObjectVersion:
    """Observed object version metadata from storage notifications."""

    identity: StorageObjectIdentity
    etag: StorageEtag
    size: int | None = None


@dataclass(frozen=True, slots=True)
class StorageEventPayload:
    """Normalized storage event used after ingress validation."""

    event_name: StorageEventName
    event_time: str
    object_version: StorageObjectVersion

    @property
    def bucket_name(self) -> StorageBucketName:
        return self.object_version.identity.bucket_name

    @property
    def object_key(self) -> StorageKey:
        return self.object_version.identity.key

    @property
    def project_path(self) -> ProjectPath:
        return self.object_version.identity.project_path

    @property
    def relative_path(self) -> str:
        return self.object_version.identity.relative_path

    @property
    def etag(self) -> StorageEtag:
        return self.object_version.etag

    @property
    def size(self) -> int | None:
        return self.object_version.size

    @property
    def is_object_created(self) -> bool:
        return self.event_name in STORAGE_OBJECT_CREATED_EVENTS

    @property
    def is_object_deleted(self) -> bool:
        return self.event_name == STORAGE_OBJECT_DELETED_EVENT


def group_storage_events_by_bucket(
    events: Iterable[StorageEventPayload],
) -> dict[StorageBucketName, tuple[StorageEventPayload, ...]]:
    """Group normalized storage events by bucket while preserving arrival order."""
    grouped_events: dict[StorageBucketName, list[StorageEventPayload]] = {}
    for storage_event in events:
        grouped_events.setdefault(storage_event.bucket_name, []).append(storage_event)
    return {
        bucket_name: tuple(bucket_events) for bucket_name, bucket_events in grouped_events.items()
    }


def normalize_storage_etag(etag: StorageEtag) -> StorageEtag:
    """Compare quoted and unquoted S3-compatible ETags the same way."""
    return etag.strip('"')


@dataclass(frozen=True, slots=True)
class RuntimeStorageEventProjectBatch:
    """Storage events grouped for one project-prefixed runtime namespace."""

    project_path: ProjectPath
    events: tuple[StorageEventPayload, ...]


@dataclass(frozen=True, slots=True)
class RuntimeStorageEventRoutingPlan:
    """Storage events split into project work and root objects that cannot route."""

    project_batches: tuple[RuntimeStorageEventProjectBatch, ...]
    skipped_events: tuple[StorageEventPayload, ...] = ()

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_events)

    @property
    def skipped_counts(self) -> RuntimeJobCounts:
        return RuntimeJobCounts(skipped=self.skipped_count)


def plan_runtime_storage_events_by_project(
    events: Iterable[StorageEventPayload],
) -> RuntimeStorageEventRoutingPlan:
    """Group storage events by project path while preserving first-seen project order."""
    events_by_project: dict[ProjectPath, list[StorageEventPayload]] = {}
    skipped_events: list[StorageEventPayload] = []

    for event in events:
        project_path = event.project_path
        if not project_path:
            skipped_events.append(event)
            continue
        events_by_project.setdefault(project_path, []).append(event)

    return RuntimeStorageEventRoutingPlan(
        project_batches=tuple(
            RuntimeStorageEventProjectBatch(
                project_path=project_path,
                events=tuple(project_events),
            )
            for project_path, project_events in events_by_project.items()
        ),
        skipped_events=tuple(skipped_events),
    )


class RuntimeStorageEventOperationKind(StrEnum):
    """Executable outcomes for a project-scoped storage event."""

    index_file = "index_file"
    delete_file = "delete_file"
    skip = "skip"


class RuntimeStorageEventSkipReason(StrEnum):
    """Reasons a project-scoped storage event should not produce work."""

    project_root = "project_root"
    unknown_event = "unknown_event"


@dataclass(frozen=True, slots=True)
class RuntimeStorageEventOperation:
    """Typed operation selected from a project-scoped storage event."""

    kind: RuntimeStorageEventOperationKind
    storage_event: StorageEventPayload
    relative_path: RuntimeFilePath | None = None
    skip_reason: RuntimeStorageEventSkipReason | None = None

    def __post_init__(self) -> None:
        if self.kind == RuntimeStorageEventOperationKind.skip:
            if self.skip_reason is None:
                raise ValueError("Skipped storage event operations require a skip reason")
            return

        if self.skip_reason is not None:
            raise ValueError("Executable storage event operations cannot include a skip reason")
        if not self.relative_path:
            raise ValueError("Executable storage event operations require a relative path")

    def require_relative_path(self) -> RuntimeFilePath:
        if not self.relative_path:
            raise RuntimeError("Storage event operation has no relative path")
        return self.relative_path


def plan_runtime_storage_event_operation(
    storage_event: StorageEventPayload,
) -> RuntimeStorageEventOperation:
    """Select the project-scoped runtime operation for one storage event."""
    relative_path = storage_event.relative_path
    if not relative_path:
        return RuntimeStorageEventOperation(
            kind=RuntimeStorageEventOperationKind.skip,
            storage_event=storage_event,
            skip_reason=RuntimeStorageEventSkipReason.project_root,
        )

    if storage_event.is_object_created:
        return RuntimeStorageEventOperation(
            kind=RuntimeStorageEventOperationKind.index_file,
            storage_event=storage_event,
            relative_path=relative_path,
        )

    if storage_event.is_object_deleted:
        return RuntimeStorageEventOperation(
            kind=RuntimeStorageEventOperationKind.delete_file,
            storage_event=storage_event,
            relative_path=relative_path,
        )

    return RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.skip,
        storage_event=storage_event,
        relative_path=relative_path,
        skip_reason=RuntimeStorageEventSkipReason.unknown_event,
    )


def plan_runtime_storage_event_operations(
    events: Iterable[StorageEventPayload],
) -> tuple[RuntimeStorageEventOperation, ...]:
    """Select project-scoped runtime operations for storage events in arrival order."""
    return tuple(plan_runtime_storage_event_operation(event) for event in events)


@dataclass(frozen=True, slots=True)
class RuntimeStorageFileIndexRequest:
    """Typed request for indexing one observed runtime storage object."""

    project_id: ProjectId
    project_external_id: ProjectExternalId
    project_name: ProjectName
    project_path: ProjectPath
    file_path: RuntimeFilePath
    object_etag: StorageEtag
    object_size: int | None = None

    @classmethod
    def from_project_event(
        cls,
        *,
        project: ProjectRuntimeReference,
        storage_event: StorageEventPayload,
    ) -> Self:
        if not storage_event.is_object_created:
            raise ValueError(
                f"Storage event {storage_event.event_name} cannot produce an index request"
            )

        file_path = storage_event.relative_path
        if not file_path:
            raise ValueError("Storage index requests require a relative file path")

        return cls(
            project_id=project.project_id,
            project_external_id=project.project_external_id,
            project_name=project.require_project_name(),
            project_path=project.project_path,
            file_path=file_path,
            object_etag=storage_event.etag,
            object_size=storage_event.size,
        )


@dataclass(frozen=True, slots=True)
class RuntimeJobCounts:
    """Common processed/failed/skipped result counters."""

    processed: int = 0
    failed: int = 0
    skipped: int = 0

    def add(self, other: RuntimeJobCounts) -> Self:
        return type(self)(
            processed=self.processed + other.processed,
            failed=self.failed + other.failed,
            skipped=self.skipped + other.skipped,
        )

    def with_processed(self, count: int = 1) -> Self:
        return type(self)(
            processed=self.processed + count,
            failed=self.failed,
            skipped=self.skipped,
        )

    def with_failed(self, count: int = 1) -> Self:
        return type(self)(
            processed=self.processed,
            failed=self.failed + count,
            skipped=self.skipped,
        )

    def with_skipped(self, count: int = 1) -> Self:
        return type(self)(
            processed=self.processed,
            failed=self.failed,
            skipped=self.skipped + count,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "failed": self.failed,
            "skipped": self.skipped,
        }


def should_include_runtime_archive_path(archive_path: StorageKey) -> bool:
    """Return whether a runtime object key should be included in an archive download."""
    parts = PurePosixPath(archive_path).parts
    if any(part.startswith(".") for part in parts):
        return False
    return "__pycache__" not in parts
