"""Tests for portable project-delete acceptance response values."""

from dataclasses import dataclass

from basic_memory.indexing.project_delete_acceptance import (
    ProjectDeleteAcceptedProject,
    ProjectDeleteAcceptedResult,
)
from basic_memory.runtime.jobs import RuntimeProjectDeleteJobRequest


@dataclass(frozen=True, slots=True)
class ProjectSource:
    id: int
    external_id: str
    name: str
    path: str
    is_default: bool | None


def project_delete_request(*, delete_notes: bool) -> RuntimeProjectDeleteJobRequest:
    return RuntimeProjectDeleteJobRequest(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="basic-memory",
        delete_notes=delete_notes,
    )


def test_project_delete_accepted_project_snapshots_basic_memory_shape() -> None:
    project = ProjectDeleteAcceptedProject.from_source(
        ProjectSource(
            id=101,
            external_id="project-main",
            name="Main",
            path="basic-memory",
            is_default=None,
        )
    )

    assert project == ProjectDeleteAcceptedProject(
        id=101,
        external_id="project-main",
        name="Main",
        path="basic-memory",
        is_default=False,
    )
    assert project.to_response_payload() == {
        "id": 101,
        "external_id": "project-main",
        "name": "Main",
        "path": "basic-memory",
        "is_default": False,
    }


def test_project_delete_accepted_result_serializes_existing_pending_response() -> None:
    old_project = ProjectDeleteAcceptedProject(
        id=101,
        external_id="project-main",
        name="Main",
        path="basic-memory",
        is_default=False,
    )

    result = ProjectDeleteAcceptedResult.queued(
        request=project_delete_request(delete_notes=True),
        job_id=123,
        old_project=old_project,
    )

    assert result.to_response_payload() == {
        "message": "Project 'Main' deletion queued",
        "status": "success",
        "deletion_status": "pending",
        "file_delete_status": "pending",
        "background": True,
        "job_id": "123",
        "old_project": {
            "id": 101,
            "external_id": "project-main",
            "name": "Main",
            "path": "basic-memory",
            "is_default": False,
        },
        "new_project": None,
    }


def test_project_delete_accepted_result_marks_file_delete_skipped() -> None:
    result = ProjectDeleteAcceptedResult.queued(
        request=project_delete_request(delete_notes=False),
        job_id="job-1",
        old_project=ProjectDeleteAcceptedProject(
            id=101,
            external_id="project-main",
            name="Main",
            path="basic-memory",
            is_default=False,
        ),
    )

    assert result.file_delete_status == "skipped"
    assert result.to_response_payload()["file_delete_status"] == "skipped"
