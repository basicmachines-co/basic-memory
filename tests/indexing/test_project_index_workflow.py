"""Tests for portable project-index workflow request values."""

from dataclasses import dataclass
from uuid import UUID

from basic_memory.indexing import (
    ProjectIndexCounters,
    ProjectIndexWorkflowCompletionUpdate,
    ProjectIndexWorkflowFailureUpdate,
    ProjectIndexWorkflowProgressUpdate,
    ProjectIndexWorkflowRequest,
    ProjectIndexWorkflowStart,
    build_project_index_workflow_completion_update,
    build_project_index_workflow_progress_update,
    build_project_index_workflow_start,
    build_project_index_workflow_stale_failure_update,
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


def test_project_index_workflow_request_serializes_existing_payload_metadata() -> None:
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

    assert request.workflow_payload_metadata() == {
        "tenant_id": str(tenant_id),
        "project_id": 42,
        "project_external_id": "external-project",
        "project_name": "Project Name",
        "project_permalink": "project-name",
        "project_path": "project",
        "force_full": True,
        "search": True,
        "embeddings": False,
    }


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
