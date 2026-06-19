"""Portable project-index workflow request values."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, Self

from basic_memory.indexing.project_index_progress import (
    ProjectIndexCounters,
    initial_project_index_counters,
    project_index_progress_text,
    should_emit_project_index_progress_event,
)
from basic_memory.runtime import (
    ProjectExternalId,
    ProjectId,
    ProjectName,
    ProjectPath,
    ProjectPermalink,
    ProjectRuntimeReference,
    RuntimeJobId,
    RuntimeWorkflowBroker,
    TenantId,
    WorkflowId,
)


class ProjectIndexWorkflowSource(Protocol):
    """Minimal source shape for project-index workflow requests."""

    tenant_id: TenantId
    project_id: ProjectId
    project_external_id: ProjectExternalId
    project_name: ProjectName | None
    project_permalink: ProjectPermalink | None
    project_path: ProjectPath
    workflow_id: WorkflowId
    force_full: bool
    search: bool
    embeddings: bool


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowRequest:
    """Project-index workflow identity and mode flags."""

    tenant_id: TenantId
    workflow_id: WorkflowId
    project: ProjectRuntimeReference
    force_full: bool
    search: bool
    embeddings: bool

    @classmethod
    def from_source(cls, source: ProjectIndexWorkflowSource) -> Self:
        """Build a workflow request from queue payloads or boundary models."""
        project_external_id = str(source.project_external_id).strip()
        if not project_external_id:
            raise ValueError(f"Project {source.project_id} is missing external_id")

        project_path = str(source.project_path).strip()
        if not project_path:
            raise ValueError(f"Project {source.project_id} is missing path")

        project_name = str(source.project_name).strip() if source.project_name else None
        project_permalink = (
            str(source.project_permalink).strip() if source.project_permalink else None
        )
        return cls(
            tenant_id=source.tenant_id,
            workflow_id=source.workflow_id,
            project=ProjectRuntimeReference(
                project_id=source.project_id,
                project_external_id=project_external_id,
                project_name=project_name,
                project_permalink=project_permalink,
                project_path=project_path,
            ),
            force_full=source.force_full,
            search=source.search,
            embeddings=source.embeddings,
        )

    def workflow_payload_metadata(self) -> dict[str, object]:
        """Serialize to the existing workflow metadata payload shape."""
        return {
            "tenant_id": str(self.tenant_id),
            **self.project.workflow_metadata(),
            "force_full": self.force_full,
            "search": self.search,
            "embeddings": self.embeddings,
        }


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowStart:
    """Portable start metadata for a project-index workflow."""

    counters: ProjectIndexCounters
    progress: str
    metadata: dict[str, object]
    attempt_event_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowProgressUpdate:
    """Portable progress metadata for a running project-index workflow."""

    counters: ProjectIndexCounters
    progress: str
    should_emit_event: bool
    metadata: dict[str, object]
    progress_event_data: dict[str, object]


def build_project_index_workflow_start(
    *,
    request: ProjectIndexWorkflowRequest,
    total_files: int,
    batch_count: int,
    batch_size: int,
    discovered_at: str,
    transport_broker: RuntimeWorkflowBroker,
    transport_entrypoint: str,
    transport_job_id: RuntimeJobId | None,
) -> ProjectIndexWorkflowStart:
    """Build the initial persisted metadata for a project-index workflow."""
    counters = initial_project_index_counters(total_files)
    progress = project_index_progress_text(counters)
    payload = request.workflow_payload_metadata()
    pgq_job_id = str(transport_job_id) if transport_job_id is not None else None
    metadata: dict[str, object] = {
        "phase": "indexing",
        "progress": progress,
        "payload": payload,
        "discovery": {
            "total_files": total_files,
            "batch_count": batch_count,
            "batch_size": batch_size,
            "discovered_at": discovered_at,
        },
        "counters": counters.to_metadata(),
        "transport": {
            "broker": transport_broker,
            "entrypoint": transport_entrypoint,
            "pgq_job_id": pgq_job_id,
        },
    }
    return ProjectIndexWorkflowStart(
        counters=counters,
        progress=progress,
        metadata=metadata,
        attempt_event_data={
            "phase": "indexing",
            "progress": progress,
            "total_files": total_files,
            "batch_count": batch_count,
            "batch_size": batch_size,
            "pgq_job_id": pgq_job_id,
            "project_id": request.project.project_id,
            "project_name": request.project.project_name,
            "project_permalink": request.project.project_permalink,
            "project_path": request.project.project_path,
        },
    )


def build_project_index_workflow_progress_update(
    *,
    metadata: Mapping[str, object],
    counters: ProjectIndexCounters,
    recorded_batch_indexes: Sequence[int] | None = None,
) -> ProjectIndexWorkflowProgressUpdate:
    """Build updated persisted metadata for a running project-index workflow."""
    progress = project_index_progress_text(counters)
    counters_metadata = counters.to_metadata()
    updated_metadata = dict(metadata)
    updated_metadata["phase"] = "indexing"
    updated_metadata["progress"] = progress
    updated_metadata["counters"] = counters_metadata
    if recorded_batch_indexes is not None:
        updated_metadata["recorded_batches"] = list(recorded_batch_indexes)

    return ProjectIndexWorkflowProgressUpdate(
        counters=counters,
        progress=progress,
        should_emit_event=should_emit_project_index_progress_event(counters),
        metadata=updated_metadata,
        progress_event_data={
            "phase": "indexing",
            "progress": progress,
            "payload": updated_metadata.get("payload") or {},
            "counters": counters_metadata,
        },
    )
