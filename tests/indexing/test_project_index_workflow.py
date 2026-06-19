"""Tests for portable project-index workflow request values."""

from dataclasses import dataclass
from uuid import UUID

from basic_memory.indexing import (
    ProjectIndexCounters,
    ProjectIndexWorkflowRequest,
    ProjectIndexWorkflowStart,
    build_project_index_workflow_start,
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
