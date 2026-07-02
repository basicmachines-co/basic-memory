"""Portable job-queue contracts for Basic Memory runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import (
    ProjectExternalId,
    ProjectId,
    ProjectName,
    ProjectPath,
    RuntimeEntityId,
    RuntimeFileChecksum,
    RuntimeFilePath,
    StorageEtag,
    StorageEventSource,
    normalize_storage_etag,
)

type TenantId = UUID
type JobEntrypoint = str
type RuntimeJobDedupeKey = str
type RuntimeJobId = str | int
type WorkflowId = UUID


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
        return runtime_job_request_from_source(
            self,
            entrypoint=entrypoint,
            payload=payload,
            priority=priority,
            execute_after=execute_after,
            headers=headers,
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
    force_full: bool = False

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
    dedupe_key: RuntimeJobDedupeKey | None = None
    headers: Mapping[str, str] | None = None


class RuntimeJobRequestSource(Protocol):
    """Minimal typed source that can become a concrete runtime queue request."""

    def dedupe_key(self) -> RuntimeJobDedupeKey: ...

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]: ...


def runtime_job_request_from_source(
    source: RuntimeJobRequestSource,
    *,
    entrypoint: JobEntrypoint,
    payload: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    priority: int = 0,
    execute_after: timedelta | None = None,
) -> RuntimeJobRequest:
    """Build a concrete queue request from a typed runtime request source."""
    return RuntimeJobRequest(
        entrypoint=entrypoint,
        payload=payload,
        priority=priority,
        execute_after=execute_after,
        dedupe_key=source.dedupe_key(),
        headers=source.routing_headers(headers),
    )


@dataclass(frozen=True, slots=True)
class RuntimeScheduledVectorSyncTask:
    """Typed scheduler handoff for syncing one entity's vector index."""

    entity_id: RuntimeEntityId
    project_id: ProjectId


@dataclass(frozen=True, slots=True)
class RuntimeScheduledProjectIndexTask:
    """Typed scheduler handoff for indexing one project."""

    project_id: ProjectId
    force_full: bool = False


class JobRuntime(Protocol):
    """Capability for enqueueing runtime jobs without depending on one queue."""

    async def enqueue(self, request: RuntimeJobRequest) -> RuntimeJobId: ...


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Internal adapter bundle selected for one runtime surface."""

    job_runtime: JobRuntime | None = None
    storage_event_source: StorageEventSource | None = None

    def require_job_runtime(self) -> JobRuntime:
        if self.job_runtime is None:
            raise RuntimeError("Job runtime is not configured")
        return self.job_runtime

    def require_storage_event_source(self) -> StorageEventSource:
        if self.storage_event_source is None:
            raise RuntimeError("Storage event source is not configured")
        return self.storage_event_source
