"""Tests for portable project-index coordinator orchestration."""

from uuid import UUID

import pytest

from basic_memory.indexing import (
    OrphanEntityCleanupResult,
    ProjectIndexCompletion,
    ProjectIndexCoordinatorResult,
    ProjectIndexWorkflowRequest,
    run_project_index_coordinator,
)
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeIndexFileBatchJobRequest,
    RuntimeJobId,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
)


def project_index_request() -> RuntimeProjectIndexJobRequest:
    return RuntimeProjectIndexJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project=ProjectRuntimeReference(
            project_id=42,
            project_external_id="project-main",
            project_name="Main",
            project_permalink="main",
            project_path="main",
        ),
        workflow_id=UUID("22222222-2222-2222-2222-222222222222"),
        force_full=False,
        search=True,
        embeddings=False,
    )


def project_index_completion() -> ProjectIndexCompletion:
    return ProjectIndexCompletion(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id="42",
        project_external_id="project-main",
        project_name="Main",
        project_permalink="main",
        project_path="main",
        workflow_id=UUID("22222222-2222-2222-2222-222222222222"),
        progress="Indexed 3/3 files, 3 succeeded",
        counters={"total": 3, "processed": 3, "succeeded": 3, "missing": 0, "failed": 0},
    )


class FakeObservedFileSource:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def list_observed_index_files(self) -> tuple[RuntimeObservedIndexFile, ...]:
        self.events.append("list")
        return (
            RuntimeObservedIndexFile(path="notes/a.md", checksum="a", size=10),
            RuntimeObservedIndexFile(path="notes/b.md", checksum="b", size=20),
            RuntimeObservedIndexFile(path="notes/c.md", checksum="c", size=30),
        )


class FakeOrphanCleaner:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.current_paths: set[str] | None = None

    async def cleanup_orphans(self, current_paths: set[str]) -> OrphanEntityCleanupResult:
        self.events.append("clean")
        self.current_paths = current_paths
        return OrphanEntityCleanupResult(
            orphan_paths=("notes/deleted.md",),
            deleted_paths=("notes/deleted.md",),
            skipped_missing_paths=(),
            skipped_changed_paths=(),
        )


class FakeWorkflowStarter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.request: ProjectIndexWorkflowRequest | None = None
        self.total_files: int | None = None
        self.batch_count: int | None = None
        self.batch_size: int | None = None
        self.coordinator_job_id: RuntimeJobId | None = None

    async def start_project_index_workflow(
        self,
        request: ProjectIndexWorkflowRequest,
        *,
        total_files: int,
        batch_count: int,
        batch_size: int,
        coordinator_job_id: RuntimeJobId | None,
    ) -> ProjectIndexCompletion | None:
        self.events.append("start")
        self.request = request
        self.total_files = total_files
        self.batch_count = batch_count
        self.batch_size = batch_size
        self.coordinator_job_id = coordinator_job_id
        return project_index_completion()


class FakeBatchEnqueuer:
    def __init__(self, events: list[str], *, fail_on_batch: int | None = None) -> None:
        self.events = events
        self.fail_on_batch = fail_on_batch
        self.requests: list[RuntimeIndexFileBatchJobRequest] = []

    async def enqueue_index_file_batch(self, request: RuntimeIndexFileBatchJobRequest) -> None:
        if request.batch_index == self.fail_on_batch:
            self.events.append(f"enqueue_failed:{request.batch_index}")
            raise RuntimeError("queue offline")
        self.events.append(f"enqueue:{request.batch_index}")
        self.requests.append(request)


class FakeFanoutFailureRecorder:
    def __init__(self, events: list[str], *, expect_record: bool = False) -> None:
        self.events = events
        self.expect_record = expect_record
        self.calls: list[tuple[UUID, str, str]] = []

    async def record_project_index_fanout_failure(
        self,
        *,
        workflow_id: UUID,
        error_message: str,
        progress: str,
    ) -> None:
        if not self.expect_record:
            raise AssertionError("fanout failure should not be recorded")
        self.events.append("failure")
        self.calls.append((workflow_id, error_message, progress))


@pytest.mark.asyncio
async def test_run_project_index_coordinator_lists_cleans_starts_and_enqueues_batches() -> None:
    events: list[str] = []
    request = project_index_request()
    orphan_cleaner = FakeOrphanCleaner(events)
    workflow_starter = FakeWorkflowStarter(events)
    batch_enqueuer = FakeBatchEnqueuer(events)

    result = await run_project_index_coordinator(
        request,
        coordinator_job_id=11,
        observed_file_source=FakeObservedFileSource(events),
        orphan_cleaner=orphan_cleaner,
        workflow_starter=workflow_starter,
        batch_enqueuer=batch_enqueuer,
        fanout_failure_recorder=FakeFanoutFailureRecorder(events),
        batch_size=2,
    )

    assert result == ProjectIndexCoordinatorResult(
        total_files=3,
        enqueued_files=3,
        enqueued_batches=2,
        deleted_files=1,
        completion=project_index_completion(),
    )
    assert events == ["list", "clean", "start", "enqueue:0", "enqueue:1"]
    assert orphan_cleaner.current_paths == {"notes/a.md", "notes/b.md", "notes/c.md"}
    assert workflow_starter.request == ProjectIndexWorkflowRequest(
        tenant_id=request.tenant_id,
        workflow_id=request.workflow_id,
        project=request.project,
        force_full=request.force_full,
        search=request.search,
        embeddings=request.embeddings,
    )
    assert workflow_starter.total_files == 3
    assert workflow_starter.batch_count == 2
    assert workflow_starter.batch_size == 2
    assert workflow_starter.coordinator_job_id == 11
    assert [queued.file_paths for queued in batch_enqueuer.requests] == [
        ("notes/a.md", "notes/b.md"),
        ("notes/c.md",),
    ]
    assert [queued.index_embeddings for queued in batch_enqueuer.requests] == [False, False]


@pytest.mark.asyncio
async def test_run_project_index_coordinator_records_fanout_failure_before_reraising() -> None:
    events: list[str] = []
    request = project_index_request()
    failure_recorder = FakeFanoutFailureRecorder(events, expect_record=True)

    with pytest.raises(RuntimeError, match="queue offline"):
        await run_project_index_coordinator(
            request,
            coordinator_job_id=11,
            observed_file_source=FakeObservedFileSource(events),
            orphan_cleaner=FakeOrphanCleaner(events),
            workflow_starter=FakeWorkflowStarter(events),
            batch_enqueuer=FakeBatchEnqueuer(events, fail_on_batch=1),
            fanout_failure_recorder=failure_recorder,
            batch_size=2,
        )

    assert events == ["list", "clean", "start", "enqueue:0", "enqueue_failed:1", "failure"]
    assert failure_recorder.calls == [
        (
            request.workflow_id,
            "Failed to enqueue project index batch jobs after 2/3 files: queue offline",
            "fan-out failed",
        )
    ]
