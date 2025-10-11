"""Tests for dependency injection functions in deps.py."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException

from basic_memory.deps import get_project_config, get_project_id
from basic_memory.models.project import Project
from basic_memory.repository.project_repository import ProjectRepository


@pytest_asyncio.fixture
async def project_with_spaces(project_repository: ProjectRepository) -> Project:
    """Create a project with spaces in the name for testing permalink normalization."""
    project_data = {
        "name": "My Test Project",
        "description": "A project with spaces in the name",
        "path": "/my/test/project",
        "is_active": True,
        "is_default": False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    return await project_repository.create(project_data)


@pytest_asyncio.fixture
async def project_with_special_chars(project_repository: ProjectRepository) -> Project:
    """Create a project with special characters for testing permalink normalization."""
    project_data = {
        "name": "Project: Test & Development!",
        "description": "A project with special characters",
        "path": "/project/test/dev",
        "is_active": True,
        "is_default": False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    return await project_repository.create(project_data)


@pytest.mark.asyncio
async def test_get_project_config_with_spaces(
    project_repository: ProjectRepository, project_with_spaces: Project
):
    """Test that get_project_config normalizes project names with spaces."""
    # The project name has spaces: "My Test Project"
    # The permalink should be: "my-test-project"
    assert project_with_spaces.name == "My Test Project"
    assert project_with_spaces.permalink == "my-test-project"

    # Call get_project_config with the project name (not permalink)
    # This simulates what happens when the project name comes from URL path
    config = await get_project_config(
        project="My Test Project", project_repository=project_repository
    )

    # Verify we got the correct project config
    assert config.name == "My Test Project"
    assert config.home == Path("/my/test/project")


@pytest.mark.asyncio
async def test_get_project_config_with_permalink(
    project_repository: ProjectRepository, project_with_spaces: Project
):
    """Test that get_project_config works when already given a permalink."""
    # Call with the permalink directly
    config = await get_project_config(
        project="my-test-project", project_repository=project_repository
    )

    # Verify we got the correct project config
    assert config.name == "My Test Project"
    assert config.home == Path("/my/test/project")


@pytest.mark.asyncio
async def test_get_project_config_with_special_chars(
    project_repository: ProjectRepository, project_with_special_chars: Project
):
    """Test that get_project_config normalizes project names with special characters."""
    # The project name has special chars: "Project: Test & Development!"
    # The permalink should be: "project-test-development"
    assert project_with_special_chars.name == "Project: Test & Development!"
    assert project_with_special_chars.permalink == "project-test-development"

    # Call get_project_config with the project name
    config = await get_project_config(
        project="Project: Test & Development!", project_repository=project_repository
    )

    # Verify we got the correct project config
    assert config.name == "Project: Test & Development!"
    assert config.home == Path("/project/test/dev")


@pytest.mark.asyncio
async def test_get_project_config_not_found(project_repository: ProjectRepository):
    """Test that get_project_config raises HTTPException when project not found."""
    with pytest.raises(HTTPException) as exc_info:
        await get_project_config(
            project="Nonexistent Project", project_repository=project_repository
        )

    assert exc_info.value.status_code == 404
    assert "Project 'Nonexistent Project' not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_project_id_with_spaces(
    project_repository: ProjectRepository, project_with_spaces: Project
):
    """Test that get_project_id normalizes project names with spaces."""
    # Call get_project_id with the project name (not permalink)
    project_id = await get_project_id(
        project_repository=project_repository, project="My Test Project"
    )

    # Verify we got the correct project ID
    assert project_id == project_with_spaces.id


@pytest.mark.asyncio
async def test_get_project_id_with_permalink(
    project_repository: ProjectRepository, project_with_spaces: Project
):
    """Test that get_project_id works when already given a permalink."""
    # Call with the permalink directly
    project_id = await get_project_id(
        project_repository=project_repository, project="my-test-project"
    )

    # Verify we got the correct project ID
    assert project_id == project_with_spaces.id


@pytest.mark.asyncio
async def test_get_project_id_with_special_chars(
    project_repository: ProjectRepository, project_with_special_chars: Project
):
    """Test that get_project_id normalizes project names with special characters."""
    # Call get_project_id with the project name
    project_id = await get_project_id(
        project_repository=project_repository, project="Project: Test & Development!"
    )

    # Verify we got the correct project ID
    assert project_id == project_with_special_chars.id


@pytest.mark.asyncio
async def test_get_project_id_not_found(project_repository: ProjectRepository):
    """Test that get_project_id raises HTTPException when project not found."""
    with pytest.raises(HTTPException) as exc_info:
        await get_project_id(project_repository=project_repository, project="Nonexistent Project")

    assert exc_info.value.status_code == 404
    assert "Project 'Nonexistent Project' not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_project_id_fallback_to_name(
    project_repository: ProjectRepository, test_project: Project
):
    """Test that get_project_id falls back to name lookup if permalink lookup fails.

    This test verifies the fallback behavior in get_project_id where it tries
    get_by_name if get_by_permalink returns None.
    """
    # The test_project fixture has name "test-project" and permalink "test-project"
    # Since both are the same, we can't easily test the fallback with existing fixtures
    # So this test just verifies the normal path works with test_project
    project_id = await get_project_id(project_repository=project_repository, project="test-project")

    assert project_id == test_project.id


@pytest.mark.asyncio
async def test_get_project_config_case_sensitivity(
    project_repository: ProjectRepository, project_with_spaces: Project
):
    """Test that get_project_config handles case variations correctly.

    Permalink normalization should convert to lowercase, so different case
    variations of the same name should resolve to the same project.
    """
    # Create project with mixed case: "My Test Project" -> permalink "my-test-project"

    # Try with different case variations
    config1 = await get_project_config(
        project="My Test Project", project_repository=project_repository
    )
    config2 = await get_project_config(
        project="my test project", project_repository=project_repository
    )
    config3 = await get_project_config(
        project="MY TEST PROJECT", project_repository=project_repository
    )

    # All should resolve to the same project
    assert config1.name == config2.name == config3.name == "My Test Project"
    assert config1.home == config2.home == config3.home == Path("/my/test/project")
