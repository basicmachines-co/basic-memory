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
from typing import Protocol, Self
from uuid import UUID

type TenantId = UUID
type ProjectId = int
type ProjectExternalId = str
type ProjectName = str
type ProjectPath = str
type ProjectPermalink = str
type StorageBucketName = str
type StorageKey = str
type StorageEtag = str
type StorageEventName = str
type StorageVersionId = str
type JobEntrypoint = str
type RuntimeJobId = str | int
type WorkflowId = UUID
type NoteExternalId = str
type SnapshotName = str
type SnapshotVersion = str

STORAGE_OBJECT_CREATED_EVENTS: frozenset[StorageEventName] = frozenset(
    {"OBJECT_CREATED_PUT", "OBJECT_CREATED_POST"}
)
STORAGE_OBJECT_DELETED_EVENT: StorageEventName = "OBJECT_DELETED"


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


class JobRuntime(Protocol):
    """Capability for enqueueing runtime jobs without depending on one queue."""

    async def enqueue(self, request: RuntimeJobRequest) -> RuntimeJobId: ...


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
