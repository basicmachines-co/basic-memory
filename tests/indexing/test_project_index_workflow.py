"""Tests for portable project-index workflow metadata planning."""

from dataclasses import dataclass
from uuid import UUID

import pytest

from basic_memory.indexing import (
    IndexFileJobResult,
    IndexFileJobStatus,
    ProjectIndexCounters,
    ProjectIndexBatchJobActivity,
    ProjectIndexBatchJobActivityUpdate,
    ProjectIndexWorkflowCompletionUpdate,
    ProjectIndexWorkflowFailureUpdate,
    ProjectIndexWorkflowProgressUpdate,
    ProjectIndexWorkflowQueued,
    ProjectIndexWorkflowRecordPlan,
    ProjectIndexWorkflowRequest,
    ProjectIndexStaleWorkflowPlan,
    ProjectIndexWorkflowStart,
    ProjectIndexWorkflowStartPlan,
    build_project_index_batch_activity_update,
    build_project_index_workflow_completion_update,
    build_project_index_workflow_progress_update,
    build_project_index_workflow_queued,
    build_project_index_workflow_start,
    build_project_index_workflow_stale_failure_update,
    plan_project_index_batch_result_record,
    plan_project_index_file_result_record,
    plan_project_index_stale_workflow,
    plan_project_index_workflow_start,
)


@dataclass(frozen=True, slots=True)
class ProjectIndexSource:
    tenant_id: UUID
    project_id: int
    project_external_id: str
    project_name: str | None
    project_permalink: str | None
    project_path: str
    workflow_id: UUID
    force_full: bool
    search: bool
    embeddings: bool


def project_index_record_metadata(
    *,
    total: int,
    processed: int = 0,
    succeeded: int = 0,
    missing: int = 0,
    failed: int = 0,
    recorded_batches: list[int] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "phase": "indexing",
        "progress": f"Indexed {processed}/{total} files, {succeeded} succeeded",
        "payload": {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "project_id": 42,
            "project_external_id": "external-project",
        },
        "discovery": {
            "total_files": total,
            "batch_count": 2,
            "batch_size": 50,
            "discovered_at": "2026-06-19T10:20:30+00:00",
        },
        "counters": {
            "total": total,
            "processed": processed,
            "succeeded": succeeded,
            "missing": missing,
            "failed": failed,
        },
    }
    if recorded_batches is not None:
        metadata["recorded_batches"] = recorded_batches
    return metadata


def test_project_index_workflow_queued_builds_metadata_event_and_logical_key() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=True,
            search=True,
            embeddings=False,
        )
    )

    queued = build_project_index_workflow_queued(
        request=request,
        transport_broker="pgq",
        transport_entrypoint="index_project",
    )

    assert queued == ProjectIndexWorkflowQueued(
        logical_key=f"index-{tenant_id}-Project Name-full-search",
        metadata={
            "job_id": str(workflow_id),
            "phase": "queued",
            "progress": "queued for index",
            "payload": {
                "tenant_id": str(tenant_id),
                "project_id": 42,
                "project_external_id": "external-project",
                "project_name": "Project Name",
                "project_permalink": "project-name",
                "project_path": "project",
                "force_full": True,
                "search": True,
                "embeddings": False,
            },
            "transport": {
                "broker": "pgq",
                "entrypoint": "index_project",
            },
        },
        queued_event_data={
            "logical_key": f"index-{tenant_id}-Project Name-full-search",
            "entrypoint": "index_project",
            "phase": "queued",
            "progress": "queued for index",
            "project_id": 42,
            "project_external_id": "external-project",
            "project_name": "Project Name",
            "project_permalink": "project-name",
            "project_path": "project",
        },
    )


def test_project_index_batch_activity_update_builds_last_activity_metadata() -> None:
    activity = ProjectIndexBatchJobActivity(
        batch_indexes=(1, 3),
        queued_count=1,
        picked_fresh_count=1,
        picked_stale_count=0,
    )

    update = build_project_index_batch_activity_update(
        metadata={
            "phase": "indexing",
            "progress": "Indexed 2/4 files, 2 succeeded",
            "payload": {"project_id": 42},
        },
        activity=activity,
        observed_at="2026-06-19T10:20:30+00:00",
    )

    assert activity.has_unfinished_jobs is True
    assert ProjectIndexBatchJobActivity.empty().has_unfinished_jobs is False
    assert update == ProjectIndexBatchJobActivityUpdate(
        activity=activity,
        metadata={
            "phase": "indexing",
            "progress": "Indexed 2/4 files, 2 succeeded",
            "payload": {"project_id": 42},
            "last_batch_job_activity": {
                "active_batches": [1, 3],
                "queued_count": 1,
                "picked_fresh_count": 1,
                "picked_stale_count": 0,
                "observed_at": "2026-06-19T10:20:30+00:00",
            },
        },
    )


def test_project_index_workflow_start_builds_existing_metadata_and_attempt_event() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=True,
            search=True,
            embeddings=False,
        )
    )

    start = build_project_index_workflow_start(
        request=request,
        total_files=4,
        batch_count=2,
        batch_size=50,
        discovered_at="2026-06-19T10:20:30+00:00",
        transport_broker="pgq",
        transport_entrypoint="index_project",
        transport_job_id=123,
    )

    assert start == ProjectIndexWorkflowStart(
        counters=ProjectIndexCounters(
            total=4,
            processed=0,
            succeeded=0,
            missing=0,
            failed=0,
        ),
        progress="Indexed 0/4 files, 0 succeeded",
        metadata={
            "phase": "indexing",
            "progress": "Indexed 0/4 files, 0 succeeded",
            "payload": {
                "tenant_id": str(tenant_id),
                "project_id": 42,
                "project_external_id": "external-project",
                "project_name": "Project Name",
                "project_permalink": "project-name",
                "project_path": "project",
                "force_full": True,
                "search": True,
                "embeddings": False,
            },
            "discovery": {
                "total_files": 4,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 4,
                "processed": 0,
                "succeeded": 0,
                "missing": 0,
                "failed": 0,
            },
            "transport": {
                "broker": "pgq",
                "entrypoint": "index_project",
                "pgq_job_id": "123",
            },
        },
        attempt_event_data={
            "phase": "indexing",
            "progress": "Indexed 0/4 files, 0 succeeded",
            "total_files": 4,
            "batch_count": 2,
            "batch_size": 50,
            "pgq_job_id": "123",
            "project_id": 42,
            "project_name": "Project Name",
            "project_permalink": "project-name",
            "project_path": "project",
        },
    )


def test_project_index_workflow_start_plan_keeps_nonempty_workflows_running() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=True,
            search=True,
            embeddings=False,
        )
    )

    plan = plan_project_index_workflow_start(
        request=request,
        total_files=4,
        batch_count=2,
        batch_size=50,
        discovered_at="2026-06-19T10:20:30+00:00",
        transport_broker="pgq",
        transport_entrypoint="index_project",
        transport_job_id=123,
    )

    assert plan.status == "running"
    assert plan.is_complete is False
    assert plan.completion_update is None
    assert plan.workflow_start.progress == "Indexed 0/4 files, 0 succeeded"
    assert plan.workflow_start.metadata["phase"] == "indexing"
    assert plan.workflow_start.metadata["transport"] == {
        "broker": "pgq",
        "entrypoint": "index_project",
        "pgq_job_id": "123",
    }
    with pytest.raises(RuntimeError, match="does not include a completion update"):
        plan.require_completion_update()


def test_project_index_workflow_start_plan_completes_empty_projects() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=False,
            search=True,
            embeddings=False,
        )
    )

    plan = plan_project_index_workflow_start(
        request=request,
        total_files=0,
        batch_count=0,
        batch_size=50,
        discovered_at="2026-06-19T10:20:30+00:00",
        transport_broker="pgq",
        transport_entrypoint="index_project",
        transport_job_id=None,
    )

    assert plan == ProjectIndexWorkflowStartPlan.complete(
        workflow_start=ProjectIndexWorkflowStart(
            counters=ProjectIndexCounters(
                total=0,
                processed=0,
                succeeded=0,
                missing=0,
                failed=0,
            ),
            progress="No files found",
            metadata={
                "phase": "indexing",
                "progress": "No files found",
                "payload": {
                    "tenant_id": str(tenant_id),
                    "project_id": 42,
                    "project_external_id": "external-project",
                    "project_name": "Project Name",
                    "project_permalink": "project-name",
                    "project_path": "project",
                    "force_full": False,
                    "search": True,
                    "embeddings": False,
                },
                "discovery": {
                    "total_files": 0,
                    "batch_count": 0,
                    "batch_size": 50,
                    "discovered_at": "2026-06-19T10:20:30+00:00",
                },
                "counters": {
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "missing": 0,
                    "failed": 0,
                },
                "transport": {
                    "broker": "pgq",
                    "entrypoint": "index_project",
                    "pgq_job_id": None,
                },
            },
            attempt_event_data={
                "phase": "indexing",
                "progress": "No files found",
                "total_files": 0,
                "batch_count": 0,
                "batch_size": 50,
                "pgq_job_id": None,
                "project_id": 42,
                "project_name": "Project Name",
                "project_permalink": "project-name",
                "project_path": "project",
            },
        ),
        completion_update=ProjectIndexWorkflowCompletionUpdate(
            counters=ProjectIndexCounters(
                total=0,
                processed=0,
                succeeded=0,
                missing=0,
                failed=0,
            ),
            progress="No files found",
            metadata={
                "phase": "completed",
                "progress": "No files found",
                "payload": {
                    "tenant_id": str(tenant_id),
                    "project_id": 42,
                    "project_external_id": "external-project",
                    "project_name": "Project Name",
                    "project_permalink": "project-name",
                    "project_path": "project",
                    "force_full": False,
                    "search": True,
                    "embeddings": False,
                },
                "discovery": {
                    "total_files": 0,
                    "batch_count": 0,
                    "batch_size": 50,
                    "discovered_at": "2026-06-19T10:20:30+00:00",
                },
                "counters": {
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "missing": 0,
                    "failed": 0,
                },
                "transport": {
                    "broker": "pgq",
                    "entrypoint": "index_project",
                    "pgq_job_id": None,
                },
                "result": {
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "missing": 0,
                    "failed": 0,
                },
            },
            completed_event_data={
                "phase": "completed",
                "progress": "No files found",
                "payload": {
                    "tenant_id": str(tenant_id),
                    "project_id": 42,
                    "project_external_id": "external-project",
                    "project_name": "Project Name",
                    "project_permalink": "project-name",
                    "project_path": "project",
                    "force_full": False,
                    "search": True,
                    "embeddings": False,
                },
                "result": {
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "missing": 0,
                    "failed": 0,
                },
            },
        ),
    )
    assert plan.is_complete is True
    assert plan.require_completion_update().metadata["phase"] == "completed"


def test_project_index_workflow_progress_update_builds_metadata_and_event_data() -> None:
    counters = ProjectIndexCounters(
        total=100,
        processed=50,
        succeeded=49,
        missing=1,
        failed=0,
    )

    update = build_project_index_workflow_progress_update(
        metadata={
            "phase": "indexing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 0,
                "succeeded": 0,
                "missing": 0,
                "failed": 0,
            },
        },
        counters=counters,
        recorded_batch_indexes=(0,),
    )

    assert update == ProjectIndexWorkflowProgressUpdate(
        counters=counters,
        progress="Indexed 50/100 files, 49 succeeded, 1 missing",
        should_emit_event=True,
        metadata={
            "phase": "indexing",
            "progress": "Indexed 50/100 files, 49 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0],
        },
        progress_event_data={
            "phase": "indexing",
            "progress": "Indexed 50/100 files, 49 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
        },
    )


def test_project_index_workflow_completion_update_builds_metadata_and_event_data() -> None:
    counters = ProjectIndexCounters(
        total=100,
        processed=100,
        succeeded=99,
        missing=1,
        failed=0,
    )

    update = build_project_index_workflow_completion_update(
        metadata={
            "phase": "indexing",
            "progress": "Indexed 50/100 files, 49 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0, 1],
        },
        counters=counters,
        progress="Indexed 100/100 files, 99 succeeded, 1 missing",
    )

    assert update == ProjectIndexWorkflowCompletionUpdate(
        counters=counters,
        progress="Indexed 100/100 files, 99 succeeded, 1 missing",
        metadata={
            "phase": "completed",
            "progress": "Indexed 100/100 files, 99 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 100,
                "succeeded": 99,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0, 1],
            "result": {
                "total": 100,
                "processed": 100,
                "succeeded": 99,
                "missing": 1,
                "failed": 0,
            },
        },
        completed_event_data={
            "phase": "completed",
            "progress": "Indexed 100/100 files, 99 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "result": {
                "total": 100,
                "processed": 100,
                "succeeded": 99,
                "missing": 1,
                "failed": 0,
            },
        },
    )


def test_project_index_file_result_record_plan_builds_progress_update() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_file_result_record(
        metadata=project_index_record_metadata(total=2),
        workflow_id=workflow_id,
        result=IndexFileJobResult(
            status=IndexFileJobStatus.processed,
            reason="file indexed: notes/a.md",
        ),
    )

    assert plan.status == "progress"
    assert plan.is_complete is False
    assert plan.should_emit_progress_event is True
    assert plan.completion_update is None
    progress_update = plan.require_progress_update()
    assert progress_update.counters == ProjectIndexCounters(
        total=2,
        processed=1,
        succeeded=1,
        missing=0,
        failed=0,
    )
    assert progress_update.metadata["phase"] == "indexing"
    assert progress_update.metadata["progress"] == "Indexed 1/2 files, 1 succeeded"
    assert progress_update.progress_event_data == {
        "phase": "indexing",
        "progress": "Indexed 1/2 files, 1 succeeded",
        "payload": {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "project_id": 42,
            "project_external_id": "external-project",
        },
        "counters": {
            "total": 2,
            "processed": 1,
            "succeeded": 1,
            "missing": 0,
            "failed": 0,
        },
    }


def test_project_index_file_result_record_plan_builds_completion_update() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_file_result_record(
        metadata=project_index_record_metadata(total=1),
        workflow_id=workflow_id,
        result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file current: notes/a.md",
        ),
    )

    assert plan.status == "complete"
    assert plan.is_complete is True
    progress_update = plan.require_progress_update()
    completion_update = plan.require_completion_update()
    assert progress_update.metadata["phase"] == "indexing"
    assert completion_update.counters == ProjectIndexCounters(
        total=1,
        processed=1,
        succeeded=1,
        missing=0,
        failed=0,
    )
    assert completion_update.metadata["phase"] == "completed"
    assert completion_update.metadata["result"] == {
        "total": 1,
        "processed": 1,
        "succeeded": 1,
        "missing": 0,
        "failed": 0,
    }
    assert completion_update.completed_event_data == {
        "phase": "completed",
        "progress": "Indexed 1/1 files, 1 succeeded",
        "payload": {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "project_id": 42,
            "project_external_id": "external-project",
        },
        "result": {
            "total": 1,
            "processed": 1,
            "succeeded": 1,
            "missing": 0,
            "failed": 0,
        },
    }


def test_project_index_batch_result_record_plan_ignores_recorded_batches() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_batch_result_record(
        metadata=project_index_record_metadata(
            total=2,
            processed=1,
            succeeded=1,
            recorded_batches=[0],
        ),
        workflow_id=workflow_id,
        batch_index=0,
        batch_count=2,
        results=[
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: notes/a.md",
            )
        ],
    )

    assert plan == ProjectIndexWorkflowRecordPlan.already_recorded()
    assert plan.should_emit_progress_event is False
    with pytest.raises(RuntimeError, match="does not include a progress update"):
        plan.require_progress_update()


def test_project_index_batch_result_record_plan_builds_completion_update() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_batch_result_record(
        metadata=project_index_record_metadata(
            total=2,
            processed=1,
            succeeded=1,
            recorded_batches=[0],
        ),
        workflow_id=workflow_id,
        batch_index=1,
        batch_count=2,
        results=[
            IndexFileJobResult(
                status=IndexFileJobStatus.missing,
                reason="file missing: notes/b.md",
            )
        ],
    )

    assert plan.status == "complete"
    assert plan.is_complete is True
    progress_update = plan.require_progress_update()
    completion_update = plan.require_completion_update()
    assert progress_update.metadata["recorded_batches"] == [0, 1]
    assert completion_update.metadata["phase"] == "completed"
    assert completion_update.metadata["recorded_batches"] == [0, 1]
    assert completion_update.metadata["result"] == {
        "total": 2,
        "processed": 2,
        "succeeded": 1,
        "missing": 1,
        "failed": 0,
    }


def test_project_index_stale_workflow_plan_keeps_active_batches_running() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    active_batch_jobs = ProjectIndexBatchJobActivity(
        batch_indexes=(1,),
        queued_count=1,
        picked_fresh_count=0,
        picked_stale_count=0,
    )

    plan = plan_project_index_stale_workflow(
        metadata=project_index_record_metadata(
            total=100,
            processed=50,
            succeeded=49,
            missing=1,
            recorded_batches=[0],
        ),
        workflow_id=workflow_id,
        active_batch_jobs=active_batch_jobs,
        observed_at="2026-06-19T10:24:00+00:00",
        last_heartbeat_at="2026-06-19T10:20:30+00:00",
        stale_before="2026-06-19T10:25:30+00:00",
    )

    assert plan.status == "keep_running"
    assert plan.should_fail is False
    assert plan.failure_update is None
    assert plan.require_activity_update() == ProjectIndexBatchJobActivityUpdate(
        activity=active_batch_jobs,
        metadata={
            "phase": "indexing",
            "progress": "Indexed 50/100 files, 49 succeeded",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0],
            "last_batch_job_activity": {
                "active_batches": [1],
                "queued_count": 1,
                "picked_fresh_count": 0,
                "picked_stale_count": 0,
                "observed_at": "2026-06-19T10:24:00+00:00",
            },
        },
    )
    with pytest.raises(RuntimeError, match="does not include a failure update"):
        plan.require_failure_update()


def test_project_index_stale_workflow_plan_builds_failure_update() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_stale_workflow(
        metadata=project_index_record_metadata(
            total=100,
            processed=50,
            succeeded=49,
            missing=1,
            recorded_batches=[0],
        ),
        workflow_id=workflow_id,
        active_batch_jobs=ProjectIndexBatchJobActivity.empty(),
        observed_at="2026-06-19T10:24:00+00:00",
        last_heartbeat_at="2026-06-19T10:20:30+00:00",
        stale_before="2026-06-19T10:25:30+00:00",
    )

    diagnostics = {
        "reason": "stale_project_index_batches",
        "missing_batches": [1],
        "recorded_batches": [0],
        "legacy_missing_batch_count": 0,
        "last_heartbeat_at": "2026-06-19T10:20:30+00:00",
        "stale_before": "2026-06-19T10:25:30+00:00",
    }
    assert plan == ProjectIndexStaleWorkflowPlan.fail(
        ProjectIndexWorkflowFailureUpdate(
            counters=ProjectIndexCounters(
                total=100,
                processed=50,
                succeeded=49,
                missing=1,
                failed=0,
            ),
            progress="Project index stalled after 50/100 files",
            error_message="Project index stalled with 1 unreported batch(es)",
            metadata={
                "phase": "failed",
                "progress": "Project index stalled after 50/100 files",
                "payload": {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "project_id": 42,
                    "project_external_id": "external-project",
                },
                "discovery": {
                    "total_files": 100,
                    "batch_count": 2,
                    "batch_size": 50,
                    "discovered_at": "2026-06-19T10:20:30+00:00",
                },
                "counters": {
                    "total": 100,
                    "processed": 50,
                    "succeeded": 49,
                    "missing": 1,
                    "failed": 0,
                },
                "recorded_batches": [0],
                "diagnostics": diagnostics,
            },
            failed_event_data={
                "phase": "failed",
                "progress": "Project index stalled after 50/100 files",
                "payload": {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "project_id": 42,
                    "project_external_id": "external-project",
                },
                "error": "Project index stalled with 1 unreported batch(es)",
                "diagnostics": diagnostics,
            },
        )
    )
    assert plan.should_fail is True
    with pytest.raises(RuntimeError, match="does not include an activity update"):
        plan.require_activity_update()


def test_project_index_workflow_stale_failure_update_builds_metadata_and_event_data() -> None:
    counters = ProjectIndexCounters(
        total=100,
        processed=50,
        succeeded=49,
        missing=1,
        failed=0,
    )

    update = build_project_index_workflow_stale_failure_update(
        metadata={
            "phase": "indexing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0],
        },
        counters=counters,
        missing_batch_indexes=(1,),
        recorded_batch_indexes=(0,),
        legacy_missing_batch_count=0,
        last_heartbeat_at="2026-06-19T10:20:30+00:00",
        stale_before="2026-06-19T10:25:30+00:00",
    )

    diagnostics = {
        "reason": "stale_project_index_batches",
        "missing_batches": [1],
        "recorded_batches": [0],
        "legacy_missing_batch_count": 0,
        "last_heartbeat_at": "2026-06-19T10:20:30+00:00",
        "stale_before": "2026-06-19T10:25:30+00:00",
    }
    assert update == ProjectIndexWorkflowFailureUpdate(
        counters=counters,
        progress="Project index stalled after 50/100 files",
        error_message="Project index stalled with 1 unreported batch(es)",
        metadata={
            "phase": "failed",
            "progress": "Project index stalled after 50/100 files",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0],
            "diagnostics": diagnostics,
        },
        failed_event_data={
            "phase": "failed",
            "progress": "Project index stalled after 50/100 files",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "error": "Project index stalled with 1 unreported batch(es)",
            "diagnostics": diagnostics,
        },
    )
