"""Portable project-index workflow enqueue orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from basic_memory.indexing.job_payloads import INDEX_PROJECT_ENTRYPOINT
from basic_memory.indexing.project_index_workflow import (
    ProjectIndexWorkflowRequest,
    build_project_index_workflow_queued,
    project_index_workflow_logical_key,
)
from basic_memory.runtime import (
    RUNTIME_ACTIVE_WORKFLOW_STATUSES,
    ProjectRuntimeReference,
    RuntimeProjectIndexJobRequest,
    RuntimeWorkflowBroker,
    RuntimeWorkflowEnqueueFailureMetadata,
    RuntimeWorkflowQueueName,
    RuntimeWorkflowStatus,
    RuntimeWorkflowType,
    TenantId,
    WorkflowId,
    plan_project_index_job_request,
)


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowHandle:
    """Minimal workflow identity needed for project-index enqueue decisions."""

    id: WorkflowId
    status: RuntimeWorkflowStatus


class ProjectIndexWorkflowStore(Protocol):
    """Durable workflow store used before the coordinator job is queued."""

    async def find_project_index_workflow_by_logical_key(
        self,
        logical_key: str,
    ) -> ProjectIndexWorkflowHandle | None: ...

    async def create_queued_project_index_workflow(
        self,
        *,
        workflow_id: WorkflowId,
        tenant_id: TenantId,
        logical_key: str,
        metadata: dict[str, object],
        queued_event_data: dict[str, object],
    ) -> ProjectIndexWorkflowHandle: ...


class ProjectIndexWorkflowJobEnqueuer(Protocol):
    """Queue adapter for project-index coordinator jobs."""

    async def enqueue_project_index_job(
        self,
        request: RuntimeProjectIndexJobRequest,
    ) -> None: ...


class ProjectIndexWorkflowEnqueueFailureRecorder(Protocol):
    """Records a workflow failure when the queue adapter rejects a new job."""

    async def record_project_index_workflow_enqueue_failure(
        self,
        *,
        workflow_id: WorkflowId,
        failure: RuntimeWorkflowEnqueueFailureMetadata,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowEnqueueRequest:
    """Request to create/reuse the visible workflow and enqueue its coordinator."""

    tenant_id: TenantId
    workflow_id: WorkflowId
    project: ProjectRuntimeReference
    force_full: bool = False
    search: bool = True
    embeddings: bool = True
    transport_broker: RuntimeWorkflowBroker = "pgq"
    transport_entrypoint: str = INDEX_PROJECT_ENTRYPOINT
    failure_queue_name: RuntimeWorkflowQueueName = "PGQ"
    failure_workflow_type: RuntimeWorkflowType = "project index"


async def run_project_index_workflow_enqueue(
    request: ProjectIndexWorkflowEnqueueRequest,
    *,
    workflow_store: ProjectIndexWorkflowStore,
    job_enqueuer: ProjectIndexWorkflowJobEnqueuer,
    failure_recorder: ProjectIndexWorkflowEnqueueFailureRecorder,
) -> WorkflowId:
    """Create/reuse the durable workflow, then queue the project-index coordinator."""
    if not request.search:
        raise ValueError("Project index workflow enqueue requires search=True")

    logical_key = project_index_workflow_logical_key(
        tenant_id=request.tenant_id,
        project_name=request.project.project_name,
        force_full=request.force_full,
        search=request.search,
        embeddings=request.embeddings,
    )
    existing_workflow = await workflow_store.find_project_index_workflow_by_logical_key(logical_key)
    if (
        existing_workflow is not None
        and existing_workflow.status in RUNTIME_ACTIVE_WORKFLOW_STATUSES
    ):
        return existing_workflow.id

    workflow_request = ProjectIndexWorkflowRequest(
        tenant_id=request.tenant_id,
        workflow_id=request.workflow_id,
        project=request.project,
        force_full=request.force_full,
        search=request.search,
        embeddings=request.embeddings,
    )
    queued_workflow = build_project_index_workflow_queued(
        request=workflow_request,
        transport_broker=request.transport_broker,
        transport_entrypoint=request.transport_entrypoint,
    )
    workflow = await workflow_store.create_queued_project_index_workflow(
        workflow_id=request.workflow_id,
        tenant_id=request.tenant_id,
        logical_key=queued_workflow.logical_key,
        metadata=queued_workflow.metadata,
        queued_event_data=queued_workflow.queued_event_data,
    )

    try:
        await job_enqueuer.enqueue_project_index_job(
            plan_project_index_job_request(
                tenant_id=request.tenant_id,
                project=request.project,
                workflow_id=request.workflow_id,
                force_full=request.force_full,
                search=request.search,
                embeddings=request.embeddings,
            )
        )
    except Exception as exc:
        failure = RuntimeWorkflowEnqueueFailureMetadata.from_error(
            queue_name=request.failure_queue_name,
            workflow_type=request.failure_workflow_type,
            error=exc,
        )
        await failure_recorder.record_project_index_workflow_enqueue_failure(
            workflow_id=request.workflow_id,
            failure=failure,
        )
        raise

    return workflow.id
