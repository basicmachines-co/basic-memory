"""Tests for portable project-index workflow request values."""

from dataclasses import dataclass
from uuid import UUID

from basic_memory.indexing import ProjectIndexWorkflowRequest


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
