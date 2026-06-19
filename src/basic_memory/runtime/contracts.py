"""Portable runtime contracts for Basic Memory deployment profiles.

These contracts describe product-core capabilities without depending on a
specific deployment backend. Local Basic Memory, hosted cloud, and future
enterprise runtimes can each provide adapters for jobs, storage events, history,
and snapshots while sharing the same typed handoff values.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

type TenantId = UUID
type ProjectId = int
type ProjectExternalId = str
type ProjectName = str
type ProjectPath = str
type ProjectPermalink = str
type RuntimeEntityId = int
type RuntimeFilePath = str
type StorageBucketName = str
type StorageKey = str
type StorageEtag = str
type StorageEventName = str
type StorageVersionId = str
type JobEntrypoint = str
type RuntimeJobId = str | int
type WorkflowId = UUID
type RuntimeWorkflowBroker = str
type RuntimeWorkflowCheckpoint = Mapping[str, object]
type RuntimeWorkflowMetadata = Mapping[str, object]
type RuntimeWorkflowMetadataPatch = Mapping[str, object]
type RuntimeWorkflowPhase = str
type RuntimeWorkflowProgress = str
type RuntimeWorkflowResult = Mapping[str, object]
type NoteExternalId = str
type RuntimeFileChecksum = str
type RuntimeNoteContentVersion = int
type RuntimeNoteContentChecksum = str
type RuntimeNoteActorKind = str
type RuntimeNoteActorName = str
type RuntimeNoteChangeSource = str
type SnapshotName = str
type SnapshotVersion = str

STORAGE_OBJECT_CREATED_EVENTS: frozenset[StorageEventName] = frozenset(
    {"OBJECT_CREATED_PUT", "OBJECT_CREATED_POST"}
)
STORAGE_OBJECT_DELETED_EVENT: StorageEventName = "OBJECT_DELETED"
WORKFLOW_EVENT_TEXT_MAX_CHARS = 4096
RUNTIME_FILE_SNAPSHOT_TIMESTAMP_MATCH_EPSILON_SECONDS = 0.001
NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR = (
    "An external file change was detected before this note could be written. "
    "Refresh to review the latest content, then retry your write if you want it to win."
)


class ProjectRuntimeSource(Protocol):
    """Minimal project shape needed by worker/runtime code."""

    @property
    def id(self) -> ProjectId: ...

    @property
    def external_id(self) -> object | None: ...

    @property
    def path(self) -> object | None: ...

    @property
    def name(self) -> object | None: ...

    @property
    def permalink(self) -> object | None: ...


class StorageEventSource(Protocol):
    """Capability for reading normalized storage events from an ingress payload."""

    def events_by_bucket(self) -> Mapping[StorageBucketName, tuple[StorageEventPayload, ...]]: ...


class RuntimeDeleteStatus(StrEnum):
    """Normal outcomes for runtime cleanup jobs."""

    deleted = "deleted"
    missing = "missing"
    skipped = "skipped"


class RuntimeNoteMaterializationStatus(StrEnum):
    """Normal outcomes for materialized note file writes."""

    written = "written"
    stale = "stale"
    missing = "missing"
    conflict = "conflict"


class RuntimeAcceptedNoteEntitySource(Protocol):
    """Minimal accepted-note entity shape needed for response payloads."""

    @property
    def external_id(self) -> str: ...

    @property
    def id(self) -> RuntimeEntityId: ...

    @property
    def title(self) -> str: ...

    @property
    def note_type(self) -> str: ...

    @property
    def content_type(self) -> str: ...

    @property
    def permalink(self) -> str | None: ...

    @property
    def file_path(self) -> RuntimeFilePath: ...

    @property
    def entity_metadata(self) -> Mapping[str, object] | None: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def updated_at(self) -> datetime: ...

    @property
    def created_by(self) -> str | None: ...

    @property
    def last_updated_by(self) -> str | None: ...


class RuntimeDeletedNoteEntitySource(Protocol):
    """Minimal deleted-note entity shape needed before row cleanup."""

    @property
    def external_id(self) -> object | None: ...

    @property
    def title(self) -> object | None: ...

    @property
    def permalink(self) -> object | None: ...


class RuntimeDeletedNoteEntityDeleteSource(RuntimeDeletedNoteEntitySource, Protocol):
    """Deleted-note entity shape needed for conditional row cleanup."""

    @property
    def id(self) -> RuntimeEntityId: ...


class RuntimeDeletedNoteEntityChecksumSource(Protocol):
    """Minimal deleted-note entity shape needed to guard file cleanup."""

    @property
    def checksum(self) -> object | None: ...


class RuntimeDeletedNoteFileChecksumSource(Protocol):
    """Minimal note_content shape needed to guard file cleanup."""

    @property
    def file_checksum(self) -> object | None: ...


class RuntimeNoteContentStateSource(Protocol):
    """Minimal note_content row shape needed for accepted-note responses."""

    @property
    def markdown_content(self) -> str: ...

    @property
    def db_version(self) -> RuntimeNoteContentVersion: ...

    @property
    def db_checksum(self) -> RuntimeNoteContentChecksum: ...

    @property
    def file_version(self) -> int | None: ...

    @property
    def file_checksum(self) -> RuntimeFileChecksum | None: ...

    @property
    def file_write_status(self) -> str: ...

    @property
    def last_source(self) -> RuntimeNoteChangeSource | None: ...

    @property
    def file_updated_at(self) -> datetime | None: ...

    @property
    def last_materialization_error(self) -> str | None: ...


class RuntimePendingNoteMaterializationSource(Protocol):
    """Minimal note_content row shape needed to queue materialization work."""

    @property
    def db_version(self) -> object: ...

    @property
    def db_checksum(self) -> object: ...

    @property
    def last_source(self) -> object | None: ...


class RuntimeMaterializedNoteSource(Protocol):
    """Minimal note_content row shape needed to clean up a materialized file."""

    @property
    def file_checksum(self) -> object | None: ...


class RuntimeNoteContentVersionSource(Protocol):
    """Minimal note_content row shape needed to compare accepted DB versions."""

    @property
    def db_version(self) -> object: ...

    @property
    def db_checksum(self) -> object: ...


class RuntimeNoteContentResourceEntitySource(Protocol):
    """Minimal entity shape needed for note-content resource reads."""

    @property
    def content_type(self) -> str: ...


class RuntimeFileChecksumReader(Protocol):
    """Capability for reading a runtime file checksum if an object exists."""

    async def exists(self, path: RuntimeFilePath) -> bool: ...

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum: ...


@dataclass(frozen=True, slots=True)
class ProjectRuntimeReference:
    """Stable project identity used by workers and storage events."""

    project_id: ProjectId
    project_external_id: ProjectExternalId
    project_path: ProjectPath
    project_name: ProjectName | None = None
    project_permalink: ProjectPermalink | None = None

    @classmethod
    def from_project(cls, project: ProjectRuntimeSource) -> Self:
        project_external_id = str(project.external_id).strip() if project.external_id else ""
        if not project_external_id:
            raise ValueError(f"Project {project.id} is missing external_id")

        project_path = str(project.path).strip() if project.path else ""
        if not project_path:
            raise ValueError(f"Project {project.id} is missing path")

        project_name = str(project.name).strip() if project.name else None
        project_permalink = str(project.permalink).strip() if project.permalink else None

        return cls(
            project_id=project.id,
            project_external_id=project_external_id,
            project_path=project_path,
            project_name=project_name,
            project_permalink=project_permalink,
        )

    def require_project_name(self) -> ProjectName:
        if not self.project_name:
            raise RuntimeError(f"Project {self.project_id} is missing name")
        return self.project_name

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize project identity for existing workflow metadata contracts."""
        return {
            "project_id": self.project_id,
            "project_external_id": self.project_external_id,
            "project_name": self.project_name,
            "project_permalink": self.project_permalink,
            "project_path": self.project_path,
        }


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
    non_markdown = "non_markdown"
    unknown_event = "unknown_event"


class RuntimeStorageFileIndexMode(StrEnum):
    """Producer mode for one runtime file-index job."""

    observed_object = "observed_object"
    current_file = "current_file"


@dataclass(frozen=True, slots=True)
class RuntimeStorageFileIndexContext:
    """Project context required before enqueueing one runtime file-index job."""

    mode: RuntimeStorageFileIndexMode
    project_external_id: ProjectExternalId | None = None
    project_name: ProjectName | None = None
    workflow_id: WorkflowId | None = None

    def require_enqueue_context(self) -> None:
        """Raise when an observed object job lacks UI-facing project context."""
        if self.mode != RuntimeStorageFileIndexMode.observed_object or self.workflow_id is not None:
            return

        if not self.project_external_id:
            raise ValueError("observed_object index jobs require project_external_id")
        if not self.project_name:
            raise ValueError("observed_object index jobs require project_name")


@dataclass(frozen=True, slots=True)
class RuntimeStorageFileIndexJobIdentity:
    """Stable queue identity for one runtime file-index job."""

    tenant_id: TenantId
    project_id: ProjectId
    file_path: RuntimeFilePath
    mode: RuntimeStorageFileIndexMode
    workflow_id: WorkflowId | None = None
    object_etag: StorageEtag | None = None
    object_size: int | None = None

    def dedupe_key(self) -> str:
        """Return the existing logical work key for file-index queue requests."""
        base = f"index-file:{self.tenant_id}:{self.project_id}:{self.file_path}"
        if self.mode == RuntimeStorageFileIndexMode.current_file:
            workflow_key = str(self.workflow_id) if self.workflow_id is not None else "current"
            return f"{base}:current:{workflow_key}"

        if self.object_etag is None:
            raise ValueError("observed_object index jobs require object metadata")
        return f"{base}:observed:{normalize_storage_etag(self.object_etag)}:{self.object_size}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the file-index job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
            }
        )
        if self.workflow_id is not None:
            routing_headers["workflow_id"] = str(self.workflow_id)
        return routing_headers

    def job_request(
        self,
        *,
        entrypoint: JobEntrypoint,
        payload: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        priority: int = 0,
        execute_after: timedelta | None = None,
    ) -> RuntimeJobRequest:
        """Build the runtime queue request for this file-index identity."""
        return RuntimeJobRequest(
            entrypoint=entrypoint,
            payload=payload,
            priority=priority,
            execute_after=execute_after,
            dedupe_key=self.dedupe_key(),
            headers=self.routing_headers(headers),
        )


@dataclass(frozen=True, slots=True)
class RuntimeStorageObjectObservation:
    """Storage object metadata observed before enqueueing one file-index job."""

    etag: StorageEtag
    size: int | None = None

    def to_file_index_job_identity(
        self,
        *,
        tenant_id: TenantId,
        project_id: ProjectId,
        file_path: RuntimeFilePath,
        workflow_id: WorkflowId | None = None,
    ) -> RuntimeStorageFileIndexJobIdentity:
        """Build the queue identity for this observed storage object."""
        return RuntimeStorageFileIndexJobIdentity(
            tenant_id=tenant_id,
            project_id=project_id,
            file_path=file_path,
            mode=RuntimeStorageFileIndexMode.observed_object,
            workflow_id=workflow_id,
            object_etag=self.etag,
            object_size=self.size,
        )


@dataclass(frozen=True, slots=True)
class RuntimeObservedIndexFile:
    """Storage metadata observed before a project-index batch is queued."""

    path: RuntimeFilePath
    checksum: RuntimeFileChecksum | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeProjectIndexJobRequest:
    """Queue-neutral request shape for coordinating a project-wide index."""

    tenant_id: TenantId
    project: ProjectRuntimeReference
    workflow_id: WorkflowId
    force_full: bool = False
    search: bool = True
    embeddings: bool = True

    def dedupe_key(self) -> str:
        """Return the logical project-index coordinator queue identity."""
        return f"index-project:{self.tenant_id}:{self.project.project_id}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the project-index coordinator."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project.project_id),
                "project_path": self.project.project_path,
                "workflow_id": str(self.workflow_id),
            }
        )
        return routing_headers


def plan_project_index_job_request(
    *,
    tenant_id: TenantId,
    project: ProjectRuntimeReference,
    workflow_id: WorkflowId,
    force_full: bool = False,
    search: bool = True,
    embeddings: bool = True,
) -> RuntimeProjectIndexJobRequest:
    """Flatten project-index workflow state into a queue-neutral request."""
    return RuntimeProjectIndexJobRequest(
        tenant_id=tenant_id,
        project=project,
        workflow_id=workflow_id,
        force_full=force_full,
        search=search,
        embeddings=embeddings,
    )


@dataclass(frozen=True, slots=True)
class RuntimeProjectDeleteJobRequest:
    """Queue-neutral request shape for hard-deleting one inactive project."""

    tenant_id: TenantId
    project_id: ProjectId
    project_external_id: ProjectExternalId
    project_name: ProjectName
    project_path: ProjectPath
    delete_notes: bool = True

    def dedupe_key(self) -> str:
        """Return the logical project-delete queue identity."""
        return f"delete-project:{self.tenant_id}:{self.project_id}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the project-delete job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
            }
        )
        return routing_headers


@dataclass(frozen=True, slots=True)
class RuntimeIndexFileBatchJobRequest:
    """Queue-neutral request shape for indexing one project file batch."""

    tenant_id: TenantId
    project: ProjectRuntimeReference
    workflow_id: WorkflowId
    batch_index: int
    batch_count: int
    file_paths: tuple[RuntimeFilePath, ...] = ()
    observed_files: tuple[RuntimeObservedIndexFile, ...] = ()
    index_embeddings: bool = True

    def dedupe_key(self) -> str:
        """Return the logical file-batch index queue identity."""
        return (
            f"index-file-batch:{self.tenant_id}:{self.project.project_id}:"
            f"{self.workflow_id}:{self.batch_index}"
        )

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the file-batch index job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project.project_id),
                "project_external_id": self.project.project_external_id,
                "project_path": self.project.project_path,
                "workflow_id": str(self.workflow_id),
            }
        )
        return routing_headers

    def target_paths(self) -> tuple[RuntimeFilePath, ...]:
        """Return target paths using observed metadata when it is available."""
        if self.observed_files:
            return tuple(observed.path for observed in self.observed_files)
        return self.file_paths


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

    if not relative_path.endswith(".md"):
        return RuntimeStorageEventOperation(
            kind=RuntimeStorageEventOperationKind.skip,
            storage_event=storage_event,
            relative_path=relative_path,
            skip_reason=RuntimeStorageEventSkipReason.non_markdown,
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
class RuntimeStorageEventProcessingResult:
    """Internal storage-event processing result for adapter handoffs."""

    counts: RuntimeJobCounts

    @classmethod
    def empty(cls) -> Self:
        return cls(counts=RuntimeJobCounts())

    @classmethod
    def from_counts(
        cls,
        *,
        processed: int = 0,
        failed: int = 0,
        skipped: int = 0,
    ) -> Self:
        return cls(
            counts=RuntimeJobCounts(
                processed=processed,
                failed=failed,
                skipped=skipped,
            )
        )

    def add(self, other: RuntimeStorageEventProcessingResult) -> Self:
        return type(self)(counts=self.counts.add(other.counts))

    def add_counts(self, counts: RuntimeJobCounts) -> Self:
        return type(self)(counts=self.counts.add(counts))

    def with_processed(self, count: int = 1) -> Self:
        return type(self)(counts=self.counts.with_processed(count))

    def with_failed(self, count: int = 1) -> Self:
        return type(self)(counts=self.counts.with_failed(count))

    def with_skipped(self, count: int = 1) -> Self:
        return type(self)(counts=self.counts.with_skipped(count))

    def as_dict(self) -> dict[str, int]:
        return self.counts.as_dict()


@dataclass(frozen=True, slots=True)
class RuntimeDeletedNoteReference:
    """Deleted note identity captured before removing its entity row."""

    external_id: NoteExternalId
    title: str
    permalink: str

    @classmethod
    def from_entity(
        cls,
        entity: RuntimeDeletedNoteEntitySource,
        *,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            external_id=required_runtime_deleted_note_text(
                entity.external_id,
                field_name="external_id",
                file_path=file_path,
            ),
            title=required_runtime_deleted_note_text(
                entity.title,
                field_name="title",
                file_path=file_path,
            ),
            permalink=required_runtime_deleted_note_text(
                entity.permalink,
                field_name="permalink",
                file_path=file_path,
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeDeletedNoteResponse:
    """Route-facing deleted-note response assembled from typed runtime identity."""

    deleted: bool
    external_id: NoteExternalId | None = None
    title: str | None = None
    permalink: str | None = None
    file_path: RuntimeFilePath | None = None
    file_delete_status: str | None = None

    @classmethod
    def missing(cls) -> Self:
        return cls(deleted=False)

    @classmethod
    def pending_file_delete(
        cls,
        *,
        entity: RuntimeDeletedNoteEntitySource,
        file_path: RuntimeFilePath,
    ) -> Self:
        deleted_note = RuntimeDeletedNoteReference.from_entity(entity, file_path=file_path)
        return cls(
            deleted=True,
            external_id=deleted_note.external_id,
            title=deleted_note.title,
            permalink=deleted_note.permalink,
            file_path=file_path,
            file_delete_status="pending",
        )

    def as_payload(self) -> dict[str, object]:
        """Serialize to the existing delete response payload shape."""
        if not self.deleted:
            return {"deleted": False}

        if self.external_id is None:
            raise RuntimeError("Deleted note response is missing external_id")
        if self.title is None:
            raise RuntimeError("Deleted note response is missing title")
        if self.permalink is None:
            raise RuntimeError("Deleted note response is missing permalink")
        if self.file_path is None:
            raise RuntimeError("Deleted note response is missing file_path")
        if self.file_delete_status is None:
            raise RuntimeError("Deleted note response is missing file_delete_status")

        return {
            "deleted": True,
            "external_id": self.external_id,
            "title": self.title,
            "permalink": self.permalink,
            "file_path": self.file_path,
            "file_delete_status": self.file_delete_status,
        }


def select_deleted_note_file_checksum(
    *,
    note_content: RuntimeDeletedNoteFileChecksumSource | None,
    entity: RuntimeDeletedNoteEntityChecksumSource,
) -> RuntimeFileChecksum | None:
    """Choose the best accepted file checksum to guard deleted-note cleanup."""
    if note_content is not None and note_content.file_checksum is not None:
        return str(note_content.file_checksum)
    if entity.checksum is not None:
        return str(entity.checksum)
    return None


def required_runtime_deleted_note_text(
    value: object,
    *,
    field_name: str,
    file_path: RuntimeFilePath,
) -> str:
    """Return required deleted-note text for downstream live-update contracts."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Deleted entity for {file_path} is missing {field_name}")
    return value.strip()


class RuntimeExternalFileDeleteAction(StrEnum):
    """Adapter work selected for an externally observed file delete."""

    missing_entity = "missing_entity"
    stale_object = "stale_object"
    delete_entity = "delete_entity"


@dataclass(frozen=True, slots=True)
class RuntimeExternalFileDeleteRequest:
    """Concrete adapter request for deleting one entity row by current file path."""

    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    deleted_note: RuntimeDeletedNoteReference


@dataclass(frozen=True, slots=True)
class RuntimeExternalFileDeletePlan:
    """Pure decision for reconciling an externally observed file delete."""

    action: RuntimeExternalFileDeleteAction
    file_path: RuntimeFilePath
    reason: str
    entity_id: RuntimeEntityId | None = None
    deleted_note: RuntimeDeletedNoteReference | None = None

    @classmethod
    def missing_entity(cls, *, file_path: RuntimeFilePath) -> Self:
        return cls(
            action=RuntimeExternalFileDeleteAction.missing_entity,
            file_path=file_path,
            reason=f"entity already absent for {file_path}",
        )

    @classmethod
    def from_existing_entity(
        cls,
        entity: RuntimeDeletedNoteEntityDeleteSource,
        *,
        file_path: RuntimeFilePath,
        object_exists: bool,
    ) -> Self:
        if object_exists:
            return cls(
                action=RuntimeExternalFileDeleteAction.stale_object,
                file_path=file_path,
                entity_id=entity.id,
                reason=f"object exists after delete event: {file_path}",
            )

        return cls(
            action=RuntimeExternalFileDeleteAction.delete_entity,
            file_path=file_path,
            entity_id=entity.id,
            deleted_note=RuntimeDeletedNoteReference.from_entity(entity, file_path=file_path),
            reason=f"delete entity for externally deleted file: {file_path}",
        )

    @property
    def should_delete_entity(self) -> bool:
        return self.action == RuntimeExternalFileDeleteAction.delete_entity

    def require_delete_request(self) -> RuntimeExternalFileDeleteRequest:
        if not self.should_delete_entity or self.entity_id is None or self.deleted_note is None:
            raise RuntimeError(
                f"External file delete plan does not delete an entity: {self.reason}"
            )
        return RuntimeExternalFileDeleteRequest(
            entity_id=self.entity_id,
            file_path=self.file_path,
            deleted_note=self.deleted_note,
        )


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
class RuntimeJobReference:
    """Queue job identity that can be shared without depending on one queue."""

    entrypoint: JobEntrypoint
    job_id: RuntimeJobId | None = None
    tenant_id: TenantId | None = None
    workflow_id: WorkflowId | None = None


@dataclass(frozen=True, slots=True)
class RuntimeJobRequest:
    """Concrete queue request built after payload validation."""

    entrypoint: JobEntrypoint
    payload: bytes | None = None
    priority: int = 0
    execute_after: timedelta | None = None
    dedupe_key: str | None = None
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowTransport:
    """Queue transport identity stored on workflow metadata."""

    broker: RuntimeWorkflowBroker
    entrypoint: JobEntrypoint

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize transport identity for existing workflow metadata contracts."""
        return {
            "broker": self.broker,
            "entrypoint": self.entrypoint,
        }


@dataclass(frozen=True, slots=True)
class RuntimeQueuedWorkflowMetadata:
    """Workflow metadata and queued event data for one runtime job handoff."""

    workflow_id: WorkflowId
    progress: RuntimeWorkflowProgress
    payload: Mapping[str, object]
    transport: RuntimeWorkflowTransport
    phase: RuntimeWorkflowPhase = "queued"

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize to the existing durable workflow metadata shape."""
        return {
            "job_id": str(self.workflow_id),
            "phase": self.phase,
            "progress": self.progress,
            "payload": dict(self.payload),
            "transport": self.transport.workflow_metadata(),
        }

    def queued_event_data(self, *, logical_key: str) -> dict[str, object]:
        """Serialize to the existing queued workflow event payload shape."""
        return {
            "logical_key": logical_key,
            "entrypoint": self.transport.entrypoint,
            "phase": self.phase,
            "progress": self.progress,
            **dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowMetadataView:
    """Typed read view over durable workflow metadata."""

    metadata: RuntimeWorkflowMetadata

    @classmethod
    def from_metadata(cls, metadata: RuntimeWorkflowMetadata | None) -> Self:
        """Build a read view from optional persisted workflow metadata."""
        return cls(metadata=dict(metadata or {}))

    @property
    def phase(self) -> RuntimeWorkflowPhase | None:
        """Return the latest machine-readable workflow phase."""
        phase = self.metadata.get("phase")
        return phase if isinstance(phase, str) else None

    @property
    def progress(self) -> RuntimeWorkflowProgress | None:
        """Return human-readable workflow progress, falling back to phase."""
        progress = self.metadata.get("progress")
        if isinstance(progress, str) and progress:
            return progress
        return self.phase

    @property
    def checkpoint(self) -> dict[str, object] | None:
        """Return copied checkpoint metadata for resumable workflow jobs."""
        checkpoint = self.metadata.get("checkpoint")
        return runtime_workflow_metadata_dict_value(checkpoint, field_name="checkpoint")

    @property
    def result(self) -> dict[str, object] | None:
        """Return copied structured result data from workflow metadata."""
        result = self.metadata.get("result")
        return runtime_workflow_metadata_dict_value(result, field_name="result")


def truncate_runtime_workflow_text(
    value: str,
    *,
    max_chars: int = WORKFLOW_EVENT_TEXT_MAX_CHARS,
) -> str:
    """Return a stable workflow text preview for durable metadata and event streams."""
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")

    if len(value) <= max_chars:
        return value

    omitted_chars = len(value) - max_chars
    suffix = f"... [truncated {omitted_chars} chars]"
    available_chars = max(max_chars - len(suffix), 0)
    return f"{value[:available_chars]}{suffix}"


def merge_runtime_workflow_metadata_patch(
    base: Mapping[str, object],
    metadata_patch: RuntimeWorkflowMetadataPatch | None,
) -> dict[str, object]:
    """Recursively merge adapter-specific workflow metadata patches."""
    patch = dict(base)
    if metadata_patch is not None:
        for key, value in metadata_patch.items():
            current = patch.get(key)
            if isinstance(current, Mapping) and isinstance(value, Mapping):
                patch[key] = merge_runtime_workflow_metadata_patch(current, value)
                continue
            patch[key] = value
    return patch


def runtime_workflow_metadata_dict_value(
    value: object,
    *,
    field_name: str,
) -> dict[str, object] | None:
    """Return a copied workflow metadata object value with string keys."""
    if not isinstance(value, dict):
        return None

    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"Workflow {field_name} metadata keys must be strings")
        copied[key] = item
    return copied


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowAttemptMetadata:
    """Workflow metadata and event data for a started runtime attempt."""

    progress: RuntimeWorkflowProgress
    metadata_patch: RuntimeWorkflowMetadataPatch | None = None
    phase: RuntimeWorkflowPhase = "running"

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable attempt metadata patch shape."""
        return merge_runtime_workflow_metadata_patch(
            {
                "phase": self.phase,
                "progress": self.progress,
            },
            self.metadata_patch,
        )

    def attempt_started_event_data(
        self,
        *,
        attempt_number: int,
        pgq_job_id: RuntimeJobId | None,
    ) -> dict[str, object]:
        """Serialize to the existing attempt-started event payload shape."""
        return {
            "attempt_number": attempt_number,
            "pgq_job_id": pgq_job_id,
            "phase": self.phase,
            "progress": self.progress,
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowProgressMetadata:
    """Workflow metadata and event data for an in-flight progress update."""

    progress: RuntimeWorkflowProgress
    phase: RuntimeWorkflowPhase | None = None
    metadata_patch: RuntimeWorkflowMetadataPatch | None = None

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable progress metadata patch shape."""
        base: dict[str, object] = {"progress": self.progress}
        if self.phase is not None:
            base["phase"] = self.phase
        return merge_runtime_workflow_metadata_patch(base, self.metadata_patch)

    def progress_event_data(self) -> dict[str, object]:
        """Serialize to the existing progress event payload shape."""
        return {
            "phase": self.phase,
            "progress": self.progress,
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowCompletionMetadata:
    """Workflow metadata and event data for a completed runtime workflow."""

    result: RuntimeWorkflowResult | None = None
    metadata_patch: RuntimeWorkflowMetadataPatch | None = None
    progress: RuntimeWorkflowProgress = "completed"

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable completion metadata patch shape."""
        base: dict[str, object] = {
            "phase": "completed",
            "progress": self.progress,
        }
        if self.result is not None:
            base["result"] = dict(self.result)
        return merge_runtime_workflow_metadata_patch(base, self.metadata_patch)

    def completed_event_data(self) -> dict[str, object]:
        """Serialize to the existing completed event payload shape."""
        return {
            "phase": "completed",
            "progress": self.progress,
            "result": dict(self.result) if self.result is not None else None,
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowFailureMetadata:
    """Workflow metadata and event data for a failed runtime workflow."""

    error_message: str
    progress: RuntimeWorkflowProgress = "failed"
    metadata_patch: RuntimeWorkflowMetadataPatch | None = None

    @property
    def error_preview(self) -> str:
        """Return the stored/evented error preview without exposing provider details."""
        return truncate_runtime_workflow_text(self.error_message)

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable failure metadata patch shape."""
        return merge_runtime_workflow_metadata_patch(
            {
                "phase": "failed",
                "progress": self.progress,
                "error_message": self.error_preview,
            },
            self.metadata_patch,
        )

    def failed_event_data(self) -> dict[str, object]:
        """Serialize to the existing failed event payload shape."""
        return {
            "phase": "failed",
            "progress": self.progress,
            "error_message": self.error_preview,
        }


class JobRuntime(Protocol):
    """Capability for enqueueing runtime jobs without depending on one queue."""

    async def enqueue(self, request: RuntimeJobRequest) -> RuntimeJobId: ...


@dataclass(frozen=True, slots=True)
class RuntimeNoteMaterializationResult:
    """Summary of one guarded note file materialization."""

    entity_id: RuntimeEntityId
    status: RuntimeNoteMaterializationStatus
    reason: str
    file_path: RuntimeFilePath | None = None
    file_checksum: RuntimeFileChecksum | None = None


@dataclass(frozen=True, slots=True)
class RuntimePendingNoteFileDelete:
    """Delete job arguments captured before a note file changes ownership."""

    project_id: ProjectId
    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None = None


@dataclass(frozen=True, slots=True)
class RuntimeProjectFileSnapshot:
    """Accepted materialized-file state captured before a project row disappears."""

    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None = None

    def to_pending_note_file_delete(self, *, project_id: ProjectId) -> RuntimePendingNoteFileDelete:
        """Return the note-file cleanup work represented by this project snapshot."""
        return RuntimePendingNoteFileDelete(
            project_id=project_id,
            entity_id=self.entity_id,
            file_path=self.file_path,
            file_checksum=self.file_checksum,
        )


@dataclass(frozen=True, slots=True)
class RuntimeDirectoryFileSnapshot:
    """Accepted materialized-file state captured before directory rows disappear."""

    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None
    last_modified_at: float | None = None
    size: int | None = None

    def to_pending_note_file_delete(self, *, project_id: ProjectId) -> RuntimePendingNoteFileDelete:
        """Return the note-file cleanup work represented by this accepted snapshot."""
        return RuntimePendingNoteFileDelete(
            project_id=project_id,
            entity_id=self.entity_id,
            file_path=self.file_path,
            file_checksum=self.file_checksum,
        )


def plan_directory_file_snapshot(
    *,
    entity_id: RuntimeEntityId,
    file_path: RuntimeFilePath,
    entity_checksum: RuntimeFileChecksum | None,
    entity_mtime: float | None,
    entity_size: int | None,
    note_file_checksum: RuntimeFileChecksum | None,
    note_file_updated_at: datetime | None,
) -> RuntimeDirectoryFileSnapshot:
    """Choose the freshest delete guard for one accepted directory-delete row."""
    note_file_updated_timestamp = (
        note_file_updated_at.timestamp() if note_file_updated_at is not None else None
    )
    accepted_last_modified_at = (
        note_file_updated_timestamp if note_file_updated_timestamp is not None else entity_mtime
    )
    accepted_checksum = (
        note_file_checksum
        if note_file_updated_timestamp is not None and note_file_checksum is not None
        else entity_checksum
    )
    timestamps_match = (
        entity_mtime is not None
        and accepted_last_modified_at is not None
        and abs(entity_mtime - accepted_last_modified_at)
        <= RUNTIME_FILE_SNAPSHOT_TIMESTAMP_MATCH_EPSILON_SECONDS
    )
    accepted_size = entity_size if entity_size is not None and timestamps_match else None

    return RuntimeDirectoryFileSnapshot(
        entity_id=entity_id,
        file_path=file_path,
        file_checksum=accepted_checksum,
        last_modified_at=accepted_last_modified_at,
        size=accepted_size,
    )


@dataclass(frozen=True, slots=True)
class RuntimeNoteFileDeleteJobRequest:
    """Queue-neutral request shape for deleting one materialized note file."""

    tenant_id: TenantId
    project_id: ProjectId
    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None = None

    def dedupe_key(self) -> str:
        """Return the logical note-file delete queue identity."""
        checksum_key = self.file_checksum or "unknown"
        return (
            f"delete-note-file:{self.tenant_id}:{self.project_id}:"
            f"{self.entity_id}:{self.file_path}:{checksum_key}"
        )

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the note-file delete job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
            }
        )
        return routing_headers


def plan_note_file_delete_job_request(
    *,
    tenant_id: TenantId,
    file_delete: RuntimePendingNoteFileDelete,
) -> RuntimeNoteFileDeleteJobRequest:
    """Flatten accepted note cleanup work into a queue-neutral delete request."""
    return RuntimeNoteFileDeleteJobRequest(
        tenant_id=tenant_id,
        project_id=file_delete.project_id,
        entity_id=file_delete.entity_id,
        file_path=file_delete.file_path,
        file_checksum=file_delete.file_checksum,
    )


def plan_previous_note_file_delete(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    existing_file_path: RuntimeFilePath | None,
    accepted_file_path: RuntimeFilePath,
    file_checksum: RuntimeFileChecksum | None,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup work when an accepted note move has materialized storage."""
    if existing_file_path is None or existing_file_path == accepted_file_path:
        return None

    if file_checksum is None:
        return None

    return RuntimePendingNoteFileDelete(
        project_id=project_id,
        entity_id=entity_id,
        file_path=existing_file_path,
        file_checksum=file_checksum,
    )


def plan_previous_materialized_note_file_delete(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    existing_file_path: RuntimeFilePath | None,
    accepted_file_path: RuntimeFilePath,
    current_note_content: RuntimeMaterializedNoteSource | None,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup work when a moved note has materialized file state."""
    file_checksum = (
        str(current_note_content.file_checksum)
        if current_note_content is not None and current_note_content.file_checksum is not None
        else None
    )
    return plan_previous_note_file_delete(
        project_id=project_id,
        entity_id=entity_id,
        existing_file_path=existing_file_path,
        accepted_file_path=accepted_file_path,
        file_checksum=file_checksum,
    )


@dataclass(frozen=True, slots=True)
class RuntimePendingNoteMaterialization:
    """Materialization job arguments captured before queue submission."""

    project_id: ProjectId
    entity_id: RuntimeEntityId
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    actor_user_profile_id: UUID | None = None
    actor_kind: RuntimeNoteActorKind | None = None
    actor_name: RuntimeNoteActorName | None = None
    source: RuntimeNoteChangeSource | None = None
    cleanup_after_write: RuntimePendingNoteFileDelete | None = None


def plan_pending_note_materialization(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    note_content: RuntimePendingNoteMaterializationSource,
    fallback_source: RuntimeNoteChangeSource,
    actor_user_profile_id: UUID | None = None,
    actor_kind: RuntimeNoteActorKind | None = None,
    actor_name: RuntimeNoteActorName | None = None,
    cleanup_after_write: RuntimePendingNoteFileDelete | None = None,
) -> RuntimePendingNoteMaterialization:
    """Build the queued materialization marker from accepted note_content state."""
    source = note_content.last_source or fallback_source
    return RuntimePendingNoteMaterialization(
        project_id=project_id,
        entity_id=entity_id,
        db_version=int(note_content.db_version),
        db_checksum=str(note_content.db_checksum),
        actor_user_profile_id=actor_user_profile_id,
        actor_kind=actor_kind,
        actor_name=actor_name,
        source=str(source) if source else None,
        cleanup_after_write=cleanup_after_write,
    )


@dataclass(frozen=True, slots=True)
class RuntimeNoteMaterializationJobRequest:
    """Queue-neutral request shape for materializing one accepted note version."""

    tenant_id: TenantId
    project_id: ProjectId
    entity_id: RuntimeEntityId
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    actor_user_profile_id: UUID | None = None
    actor_kind: RuntimeNoteActorKind | None = None
    actor_name: RuntimeNoteActorName | None = None
    source: RuntimeNoteChangeSource | None = None
    cleanup_file_path: RuntimeFilePath | None = None
    cleanup_file_checksum: RuntimeFileChecksum | None = None

    def dedupe_key(self) -> str:
        """Return the logical materialization queue identity."""
        return (
            f"materialize-note-file:{self.tenant_id}:{self.project_id}:"
            f"{self.entity_id}:{self.db_version}:{self.db_checksum}"
        )

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the materialization job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
            }
        )
        return routing_headers


def note_content_matches_materialization_request(
    note_content: RuntimeNoteContentVersionSource,
    request: RuntimeNoteMaterializationJobRequest,
) -> bool:
    """Return whether note_content still matches one queued materialization request."""
    return (
        int(note_content.db_version) == request.db_version
        and str(note_content.db_checksum) == request.db_checksum
    )


def plan_note_materialization_cleanup_file_delete(
    request: RuntimeNoteMaterializationJobRequest,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup work carried by one materialization request."""
    if request.cleanup_file_path is None:
        return None
    return RuntimePendingNoteFileDelete(
        project_id=request.project_id,
        entity_id=request.entity_id,
        file_path=request.cleanup_file_path,
        file_checksum=request.cleanup_file_checksum,
    )


def plan_note_materialization_job_request(
    *,
    tenant_id: TenantId,
    materialization: RuntimePendingNoteMaterialization,
) -> RuntimeNoteMaterializationJobRequest:
    """Flatten accepted note follow-up work into a queue-neutral materialization request."""
    cleanup = materialization.cleanup_after_write
    return RuntimeNoteMaterializationJobRequest(
        tenant_id=tenant_id,
        project_id=materialization.project_id,
        entity_id=materialization.entity_id,
        db_version=materialization.db_version,
        db_checksum=materialization.db_checksum,
        actor_user_profile_id=materialization.actor_user_profile_id,
        actor_kind=materialization.actor_kind,
        actor_name=materialization.actor_name,
        source=materialization.source,
        cleanup_file_path=cleanup.file_path if cleanup is not None else None,
        cleanup_file_checksum=cleanup.file_checksum if cleanup is not None else None,
    )


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedNoteChange[PayloadT]:
    """Accepted note response plus any post-commit runtime follow-up work."""

    status_code: int
    payload: PayloadT
    materialization: RuntimePendingNoteMaterialization | None = None
    file_delete: RuntimePendingNoteFileDelete | None = None


@dataclass(frozen=True, slots=True)
class RuntimeNoteContentState:
    """Accepted note_content row state before response serialization."""

    markdown_content: str
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    file_version: int | None
    file_checksum: RuntimeFileChecksum | None
    file_write_status: str
    last_source: RuntimeNoteChangeSource | None
    file_updated_at: datetime | None
    last_materialization_error: str | None

    @classmethod
    def from_source(cls, source: RuntimeNoteContentStateSource) -> Self:
        """Build typed runtime state from a loaded note_content source row."""
        return cls(
            markdown_content=source.markdown_content,
            db_version=source.db_version,
            db_checksum=source.db_checksum,
            file_version=source.file_version,
            file_checksum=source.file_checksum,
            file_write_status=source.file_write_status,
            last_source=source.last_source,
            file_updated_at=source.file_updated_at,
            last_materialization_error=source.last_materialization_error,
        )


def plan_accepted_note_response(
    *,
    entity: RuntimeAcceptedNoteEntitySource,
    note_content: RuntimeNoteContentStateSource,
    fallback_source: RuntimeNoteChangeSource,
) -> RuntimeAcceptedNoteResponse:
    """Build an accepted-note response from accepted note_content state."""
    note_content_state = RuntimeNoteContentState.from_source(note_content)
    if note_content_state.last_source is None:
        note_content_state = replace(note_content_state, last_source=fallback_source)
    return RuntimeAcceptedNoteResponse.from_entity_and_content_state(
        entity,
        note_content_state,
    )


@dataclass(frozen=True, slots=True)
class RuntimeNoteContentResource:
    """Resource response state for one accepted markdown note."""

    content: str
    content_type: str

    @classmethod
    def from_entity_and_content_state(
        cls,
        entity: RuntimeNoteContentResourceEntitySource,
        note_content: RuntimeNoteContentState,
    ) -> Self:
        """Build a resource response from typed note_content state."""
        return cls(
            content=note_content.markdown_content,
            content_type=entity.content_type,
        )


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedNoteResponse:
    """Accepted note response state before HTTP serialization."""

    external_id: str
    entity_id: RuntimeEntityId
    title: str
    note_type: str
    content_type: str
    permalink: str | None
    file_path: RuntimeFilePath
    markdown_content: str
    entity_metadata: Mapping[str, object] | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    last_updated_by: str | None
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    file_version: int | None
    file_checksum: RuntimeFileChecksum | None
    file_write_status: str
    last_source: str | None
    file_updated_at: datetime | None
    last_materialization_error: str | None

    @classmethod
    def from_entity(
        cls,
        entity: RuntimeAcceptedNoteEntitySource,
        *,
        markdown_content: str,
        db_version: RuntimeNoteContentVersion,
        db_checksum: RuntimeNoteContentChecksum,
        file_version: int | None,
        file_checksum: RuntimeFileChecksum | None,
        file_write_status: str,
        last_source: str | None,
        file_updated_at: datetime | None,
        last_materialization_error: str | None,
    ) -> Self:
        """Build accepted-note response state from a loaded entity plus note_content markers."""
        return cls(
            external_id=entity.external_id,
            entity_id=entity.id,
            title=entity.title,
            note_type=entity.note_type,
            content_type=entity.content_type,
            permalink=entity.permalink,
            file_path=entity.file_path,
            markdown_content=markdown_content,
            entity_metadata=entity.entity_metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            created_by=entity.created_by,
            last_updated_by=entity.last_updated_by,
            db_version=db_version,
            db_checksum=db_checksum,
            file_version=file_version,
            file_checksum=file_checksum,
            file_write_status=file_write_status,
            last_source=last_source,
            file_updated_at=file_updated_at,
            last_materialization_error=last_materialization_error,
        )

    @classmethod
    def from_entity_and_content_state(
        cls,
        entity: RuntimeAcceptedNoteEntitySource,
        note_content: RuntimeNoteContentState,
    ) -> Self:
        """Build accepted-note response state from an entity and typed note_content state."""
        return cls.from_entity(
            entity,
            markdown_content=note_content.markdown_content,
            db_version=note_content.db_version,
            db_checksum=note_content.db_checksum,
            file_version=note_content.file_version,
            file_checksum=note_content.file_checksum,
            file_write_status=note_content.file_write_status,
            last_source=note_content.last_source,
            file_updated_at=note_content.file_updated_at,
            last_materialization_error=note_content.last_materialization_error,
        )

    def to_response_payload(self) -> dict[str, object]:
        """Serialize to the existing v2 entity-plus-note-content response shape."""
        payload: dict[str, object] = {
            "external_id": self.external_id,
            "id": self.entity_id,
            "title": self.title,
            "note_type": self.note_type,
            "content_type": self.content_type,
            "permalink": self.permalink,
            "file_path": self.file_path,
            "content": self.markdown_content,
            "entity_metadata": (
                dict(self.entity_metadata) if self.entity_metadata is not None else None
            ),
            "observations": [],
            "relations": [],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "created_by": self.created_by,
            "last_updated_by": self.last_updated_by,
            "api_version": "v2",
            "db_version": self.db_version,
            "db_checksum": self.db_checksum,
            "file_version": self.file_version,
            "file_checksum": self.file_checksum,
            "file_write_status": self.file_write_status,
            "last_source": self.last_source,
            "file_updated_at": (
                self.file_updated_at.isoformat() if self.file_updated_at is not None else None
            ),
            "last_materialization_error": self.last_materialization_error,
        }
        if self.file_write_status == "external_change_detected":
            payload["sync_error"] = NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeFileDeleteResult:
    """Summary of one guarded materialized-file cleanup."""

    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    status: RuntimeDeleteStatus
    reason: str

    @classmethod
    def no_accepted_checksum(
        cls,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            entity_id=entity_id,
            file_path=file_path,
            status=RuntimeDeleteStatus.skipped,
            reason=f"no accepted file checksum for {file_path}",
        )

    @classmethod
    def already_absent(
        cls,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            entity_id=entity_id,
            file_path=file_path,
            status=RuntimeDeleteStatus.missing,
            reason=f"file already absent: {file_path}",
        )

    @classmethod
    def changed_before_delete(
        cls,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            entity_id=entity_id,
            file_path=file_path,
            status=RuntimeDeleteStatus.skipped,
            reason=f"file changed before delete: {file_path}",
        )

    @classmethod
    def deleted(
        cls,
        *,
        entity_id: RuntimeEntityId,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            entity_id=entity_id,
            file_path=file_path,
            status=RuntimeDeleteStatus.deleted,
            reason=f"file deleted: {file_path}",
        )


@dataclass(frozen=True, slots=True)
class RuntimeNoteFileDeletePlan:
    """Pure cleanup decision before a runtime adapter deletes a materialized file."""

    result: RuntimeFileDeleteResult
    actual_checksum: RuntimeFileChecksum | None

    @property
    def should_delete_file(self) -> bool:
        """Return whether the adapter may perform the storage delete."""
        return self.result.status == RuntimeDeleteStatus.deleted


def plan_note_file_delete_cleanup(
    *,
    entity_id: RuntimeEntityId,
    file_path: RuntimeFilePath,
    accepted_checksum: RuntimeFileChecksum | None,
    actual_checksum: RuntimeFileChecksum | None,
) -> RuntimeNoteFileDeletePlan:
    """Select the safe cleanup outcome for one materialized note file."""
    if accepted_checksum is None:
        return RuntimeNoteFileDeletePlan(
            result=RuntimeFileDeleteResult.no_accepted_checksum(
                entity_id=entity_id,
                file_path=file_path,
            ),
            actual_checksum=actual_checksum,
        )

    if actual_checksum is None:
        return RuntimeNoteFileDeletePlan(
            result=RuntimeFileDeleteResult.already_absent(
                entity_id=entity_id,
                file_path=file_path,
            ),
            actual_checksum=actual_checksum,
        )

    if actual_checksum != accepted_checksum:
        return RuntimeNoteFileDeletePlan(
            result=RuntimeFileDeleteResult.changed_before_delete(
                entity_id=entity_id,
                file_path=file_path,
            ),
            actual_checksum=actual_checksum,
        )

    return RuntimeNoteFileDeletePlan(
        result=RuntimeFileDeleteResult.deleted(
            entity_id=entity_id,
            file_path=file_path,
        ),
        actual_checksum=actual_checksum,
    )


@dataclass(frozen=True, slots=True)
class RuntimeExpectedFileState:
    """The storage object state a guarded write expects to find."""

    file_path: RuntimeFilePath
    expected_checksum: RuntimeFileChecksum | None


@dataclass(frozen=True, slots=True)
class RuntimeFileConflict:
    """Storage object state that does not match a guarded write."""

    file_path: RuntimeFilePath
    expected_checksum: RuntimeFileChecksum | None
    actual_checksum: RuntimeFileChecksum

    @property
    def message(self) -> str:
        if self.expected_checksum is None:
            return (
                f"Refusing to overwrite unexpected file at {self.file_path}: "
                f"expected no existing object, found checksum {self.actual_checksum}"
            )
        return (
            f"Refusing to overwrite unexpected file at {self.file_path}: "
            f"expected checksum {self.expected_checksum}, found {self.actual_checksum}"
        )


class RuntimeFileConflictError(RuntimeError):
    """Raised when storage no longer matches the expected file state."""

    def __init__(self, conflict: RuntimeFileConflict) -> None:
        super().__init__(conflict.message)
        self.conflict = conflict
        self.file_path = conflict.file_path
        self.expected_checksum = conflict.expected_checksum
        self.actual_checksum = conflict.actual_checksum


async def read_runtime_file_checksum(
    reader: RuntimeFileChecksumReader,
    file_path: RuntimeFilePath,
) -> RuntimeFileChecksum | None:
    """Return the current runtime file checksum, or None when absent."""
    if not await reader.exists(file_path):
        return None
    return await reader.compute_checksum(file_path)


async def assert_runtime_file_matches_expected(
    reader: RuntimeFileChecksumReader,
    expected: RuntimeExpectedFileState,
) -> None:
    """Raise when a guarded write would overwrite an unexpected runtime file."""
    actual_checksum = await read_runtime_file_checksum(reader, expected.file_path)
    if actual_checksum is None:
        return
    if expected.expected_checksum is None or actual_checksum != expected.expected_checksum:
        raise RuntimeFileConflictError(
            RuntimeFileConflict(
                file_path=expected.file_path,
                expected_checksum=expected.expected_checksum,
                actual_checksum=actual_checksum,
            )
        )


@dataclass(frozen=True, slots=True)
class RuntimeProjectDeleteResult:
    """Summary of one project cleanup run."""

    project_id: ProjectId
    project_external_id: ProjectExternalId
    status: RuntimeDeleteStatus
    deleted_project: bool
    deleted_files: int
    skipped_files: int
    missing_files: int
    reason: str

    @classmethod
    def from_file_results(
        cls,
        *,
        project_id: ProjectId,
        project_external_id: ProjectExternalId,
        status: RuntimeDeleteStatus,
        deleted_project: bool,
        file_results: list[RuntimeFileDeleteResult],
        reason: str,
    ) -> "RuntimeProjectDeleteResult":
        """Build aggregate project cleanup counters from guarded file deletes."""
        return cls(
            project_id=project_id,
            project_external_id=project_external_id,
            status=status,
            deleted_project=deleted_project,
            deleted_files=sum(
                1 for result in file_results if result.status == RuntimeDeleteStatus.deleted
            ),
            skipped_files=sum(
                1 for result in file_results if result.status == RuntimeDeleteStatus.skipped
            ),
            missing_files=sum(
                1 for result in file_results if result.status == RuntimeDeleteStatus.missing
            ),
            reason=reason,
        )


class NoteHistoryProvider(Protocol):
    """Capability for reading materialized note file history."""

    async def list_versions(
        self,
        reference: NoteHistoryReference,
        *,
        max_keys: int,
        key_marker: StorageKey | None = None,
        version_id_marker: StorageVersionId | None = None,
    ) -> NoteHistoryPage: ...

    async def read_version(
        self,
        reference: NoteHistoryReference,
        version_id: StorageVersionId,
        *,
        max_bytes: int | None = None,
    ) -> bytes: ...


class NoteHistoryVersionSource(Protocol):
    """Storage-version shape needed to build portable note history."""

    @property
    def version_id(self) -> StorageVersionId: ...

    @property
    def key(self) -> StorageKey: ...

    @property
    def is_latest(self) -> bool: ...

    @property
    def last_modified(self) -> datetime: ...

    @property
    def size(self) -> int: ...

    @property
    def etag(self) -> StorageEtag: ...


class NoteHistoryPageSource(Protocol):
    """Storage-version page shape needed to build portable note history."""

    @property
    def versions(self) -> Iterable[NoteHistoryVersionSource]: ...

    @property
    def next_key_marker(self) -> StorageKey | None: ...

    @property
    def next_version_id_marker(self) -> StorageVersionId | None: ...


class SnapshotProvider(Protocol):
    """Capability for creating and reading bucket snapshot state."""

    async def create_snapshot(
        self,
        bucket_name: StorageBucketName,
        name: SnapshotName,
    ) -> SnapshotVersion: ...

    async def list_objects(
        self,
        reference: SnapshotReference,
        *,
        prefix: StorageKey = "",
    ) -> tuple[SnapshotObjectReference, ...]: ...

    async def list_all_objects(
        self,
        reference: SnapshotReference,
        *,
        prefix: StorageKey = "",
    ) -> tuple[SnapshotObjectReference, ...]: ...

    async def read_object(
        self,
        reference: SnapshotReference,
        key: StorageKey,
    ) -> bytes: ...

    async def restore_object(
        self,
        reference: SnapshotReference,
        key: StorageKey,
    ) -> None: ...


type SnapshotProviderFactory[SnapshotProviderInputT] = Callable[
    [SnapshotProviderInputT], SnapshotProvider
]
type NoteHistoryProviderFactory[NoteHistoryProviderInputT] = Callable[
    [NoteHistoryProviderInputT], NoteHistoryProvider
]


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities[SnapshotProviderInputT, NoteHistoryProviderInputT]:
    """Internal adapter bundle selected for one runtime surface."""

    job_runtime: JobRuntime | None = None
    storage_event_source: StorageEventSource | None = None
    snapshot_provider_factory: SnapshotProviderFactory[SnapshotProviderInputT] | None = None
    note_history_provider_factory: NoteHistoryProviderFactory[NoteHistoryProviderInputT] | None = (
        None
    )

    def require_job_runtime(self) -> JobRuntime:
        if self.job_runtime is None:
            raise RuntimeError("Job runtime is not configured")
        return self.job_runtime

    def require_storage_event_source(self) -> StorageEventSource:
        if self.storage_event_source is None:
            raise RuntimeError("Storage event source is not configured")
        return self.storage_event_source

    def require_snapshot_provider_factory(
        self,
    ) -> SnapshotProviderFactory[SnapshotProviderInputT]:
        if self.snapshot_provider_factory is None:
            raise RuntimeError("Snapshot provider factory is not configured")
        return self.snapshot_provider_factory

    def require_note_history_provider_factory(
        self,
    ) -> NoteHistoryProviderFactory[NoteHistoryProviderInputT]:
        if self.note_history_provider_factory is None:
            raise RuntimeError("Note history provider factory is not configured")
        return self.note_history_provider_factory


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


@dataclass(frozen=True, slots=True)
class NoteHistoryReference:
    """Note history identity independent of a concrete storage provider."""

    tenant_id: TenantId
    bucket_name: StorageBucketName
    project_external_id: ProjectExternalId
    note_external_id: NoteExternalId
    file_path: str
    object_key: StorageKey


@dataclass(frozen=True, slots=True)
class NoteHistoryVersion:
    """One materialized object version in a note's file history."""

    version_id: StorageVersionId
    key: StorageKey
    is_latest: bool
    last_modified: datetime
    size: int
    etag: StorageEtag

    @classmethod
    def from_source(cls, source: NoteHistoryVersionSource) -> Self:
        """Build a portable note-history version from storage-provider metadata."""
        return cls(
            version_id=source.version_id,
            key=source.key,
            is_latest=source.is_latest,
            last_modified=source.last_modified,
            size=source.size,
            etag=source.etag,
        )


@dataclass(frozen=True, slots=True)
class NoteHistoryPage:
    """One page of note file history plus storage pagination markers."""

    versions: tuple[NoteHistoryVersion, ...]
    next_key_marker: StorageKey | None = None
    next_version_id_marker: StorageVersionId | None = None

    @classmethod
    def from_source(cls, source: NoteHistoryPageSource) -> Self:
        """Build a portable note-history page from storage-provider pagination."""
        return cls(
            versions=tuple(NoteHistoryVersion.from_source(version) for version in source.versions),
            next_key_marker=source.next_key_marker,
            next_version_id_marker=source.next_version_id_marker,
        )


@dataclass(frozen=True, slots=True)
class SnapshotReference:
    """Snapshot identity independent of concrete storage APIs."""

    tenant_id: TenantId
    bucket_name: StorageBucketName
    snapshot_name: SnapshotName
    snapshot_version: SnapshotVersion


@dataclass(frozen=True, slots=True)
class SnapshotObjectReference:
    """One object visible in a bucket snapshot listing."""

    key: StorageKey
    size: int
    last_modified: datetime
    etag: StorageEtag
