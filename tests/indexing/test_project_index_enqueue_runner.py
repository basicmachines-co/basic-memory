from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest

from basic_memory.indexing.project_index_enqueue_runner import (
    ProjectIndexWorkflowEnqueueFailureRecorder,
    ProjectIndexWorkflowEnqueueRequest,
    ProjectIndexWorkflowHandle,
    ProjectIndexWorkflowJobEnqueuer,
    ProjectIndexWorkflowStore,
    run_project_index_workflow_enqueue,
)
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeProjectIndexJobRequest,
    RuntimeWorkflowEnqueueFailureMetadata,
)


@dataclass(frozen=True, slots=True)
class FakeWorkflow:
    id: UUID
    status: str


class FakeWorkflowStore(ProjectIndexWorkflowStore):
    def __init__(self, existing: FakeWorkflow | None = None) -> None:
        self.existing = existing
        self.logical_keys: list[str] = []
        self.created: list[tuple[UUID, UUID, str, dict[str, object]]] = []

    async def find_project_index_workflow_by_logical_key(
        self,
        logical_key: str,
    ) -> ProjectIndexWorkflowHandle | None:
        self.logical_keys.append(logical_key)
        if self.existing is None:
            return None
        return ProjectIndexWorkflowHandle(id=self.existing.id, status=self.existing.status)

    async def create_queued_project_index_workflow(
        self,
        *,
        workflow_id: UUID,
        tenant_id: UUID,
        logical_key: str,
        metadata: dict[str, object],
        queued_event_data: dict[str, object],
    ) -> ProjectIndexWorkflowHandle:
        self.created.append((workflow_id, tenant_id, logical_key, metadata))
        assert queued_event_data["logical_key"] == logical_key
        return ProjectIndexWorkflowHandle(id=workflow_id, status="queued")


class FakeProjectIndexJobEnqueuer(ProjectIndexWorkflowJobEnqueuer):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[RuntimeProjectIndexJobRequest] = []

    async def enqueue_project_index_job(
        self,
        request: RuntimeProjectIndexJobRequest,
    ) -> None:
        self.requests.append(request)
        if self.error is not None:
            raise self.error


class FakeFailureRecorder(ProjectIndexWorkflowEnqueueFailureRecorder):
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, RuntimeWorkflowEnqueueFailureMetadata]] = []

    async def record_project_index_workflow_enqueue_failure(
        self,
        *,
        workflow_id: UUID,
        failure: RuntimeWorkflowEnqueueFailureMetadata,
    ) -> None:
        self.calls.append((workflow_id, failure))


def project() -> ProjectRuntimeReference:
    return ProjectRuntimeReference(
        project_id=42,
        project_external_id="external-project",
        project_name="Project Name",
        project_permalink="project-name",
        project_path="project",
    )


def enqueue_request() -> ProjectIndexWorkflowEnqueueRequest:
    return ProjectIndexWorkflowEnqueueRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        workflow_id=UUID("22222222-2222-2222-2222-222222222222"),
        project=project(),
        force_full=True,
        embeddings=False,
    )


@pytest.mark.asyncio
async def test_project_index_workflow_enqueue_reuses_active_workflow() -> None:
    existing = FakeWorkflow(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        status="running",
    )
    store = FakeWorkflowStore(existing=existing)
    job_enqueuer = FakeProjectIndexJobEnqueuer()

    workflow_id = await run_project_index_workflow_enqueue(
        enqueue_request(),
        workflow_store=store,
        job_enqueuer=job_enqueuer,
        failure_recorder=FakeFailureRecorder(),
    )

    assert workflow_id == existing.id
    assert store.logical_keys == [f"index-{enqueue_request().tenant_id}-Project Name-full-search"]
    assert store.created == []
    assert job_enqueuer.requests == []


@pytest.mark.asyncio
async def test_project_index_workflow_enqueue_creates_workflow_and_queues_job() -> None:
    request = enqueue_request()
    store = FakeWorkflowStore()
    job_enqueuer = FakeProjectIndexJobEnqueuer()

    workflow_id = await run_project_index_workflow_enqueue(
        request,
        workflow_store=store,
        job_enqueuer=job_enqueuer,
        failure_recorder=FakeFailureRecorder(),
    )

    assert workflow_id == request.workflow_id
    assert len(store.created) == 1
    created_workflow_id, tenant_id, logical_key, metadata = store.created[0]
    assert created_workflow_id == request.workflow_id
    assert tenant_id == request.tenant_id
    assert logical_key == f"index-{request.tenant_id}-Project Name-full-search"
    assert metadata["phase"] == "queued"
    assert metadata["payload"] == {
        "tenant_id": str(request.tenant_id),
        "project_id": 42,
        "project_external_id": "external-project",
        "project_name": "Project Name",
        "project_permalink": "project-name",
        "project_path": "project",
        "force_full": True,
        "search": True,
        "embeddings": False,
    }
    assert job_enqueuer.requests == [
        RuntimeProjectIndexJobRequest(
            tenant_id=request.tenant_id,
            project=request.project,
            workflow_id=request.workflow_id,
            force_full=True,
            embeddings=False,
        )
    ]


@pytest.mark.asyncio
async def test_project_index_workflow_enqueue_records_queue_failure() -> None:
    request = enqueue_request()
    failure_recorder = FakeFailureRecorder()

    with pytest.raises(RuntimeError, match="queue offline"):
        await run_project_index_workflow_enqueue(
            request,
            workflow_store=FakeWorkflowStore(),
            job_enqueuer=FakeProjectIndexJobEnqueuer(error=RuntimeError("queue offline")),
            failure_recorder=failure_recorder,
        )

    assert len(failure_recorder.calls) == 1
    workflow_id, failure = failure_recorder.calls[0]
    assert workflow_id == request.workflow_id
    assert failure.error_message == "Failed to enqueue PGQ project index: queue offline"
    assert failure.progress == "enqueue failed"
