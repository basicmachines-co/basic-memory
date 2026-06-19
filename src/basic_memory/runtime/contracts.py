"""Portable runtime contracts for Basic Memory deployment profiles.

These contracts describe product-core capabilities without depending on a
specific deployment backend. Local Basic Memory, hosted cloud, and future
enterprise runtimes can each provide adapters for jobs, storage events, history,
and snapshots while sharing the same typed handoff values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
    """Merge adapter-specific workflow metadata without changing existing precedence."""
    patch = dict(base)
    if metadata_patch is not None:
        patch.update(metadata_patch)
    return patch


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


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedNoteChange[PayloadT]:
    """Accepted note response plus any post-commit runtime follow-up work."""

    status_code: int
    payload: PayloadT
    materialization: RuntimePendingNoteMaterialization | None = None
    file_delete: RuntimePendingNoteFileDelete | None = None


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


@dataclass(frozen=True, slots=True)
class NoteHistoryPage:
    """One page of note file history plus storage pagination markers."""

    versions: tuple[NoteHistoryVersion, ...]
    next_key_marker: StorageKey | None = None
    next_version_id_marker: StorageVersionId | None = None


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
