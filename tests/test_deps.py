"""Tests for dependency injection functions in the deps package."""

import pytest
from fastapi import HTTPException

from basic_memory.deps import validate_project_external_id
from basic_memory.models.project import Project
from basic_memory.repository.project_repository import ProjectRepository


@pytest.mark.asyncio
async def test_validate_project_external_id_success(
    project_repository: ProjectRepository, test_project: Project, session_maker
):
    """validate_project_external_id resolves the internal id from the external UUID."""
    project_id = await validate_project_external_id(
        session_maker=session_maker,
        project_id=test_project.external_id,
        project_repository=project_repository,
    )

    assert project_id == test_project.id


@pytest.mark.asyncio
async def test_validate_project_external_id_not_found(
    project_repository: ProjectRepository, session_maker
):
    """validate_project_external_id raises HTTPException when no project matches."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(HTTPException) as exc_info:
        await validate_project_external_id(
            session_maker=session_maker,
            project_id=fake_uuid,
            project_repository=project_repository,
        )

    assert exc_info.value.status_code == 404
    assert f"Project with external_id '{fake_uuid}' not found" in exc_info.value.detail
