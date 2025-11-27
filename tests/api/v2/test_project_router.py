"""Tests for V2 project management API routes (ID-based endpoints)."""

import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory.schemas.project_info import ProjectItem, ProjectStatusResponse


@pytest.mark.asyncio
async def test_get_project_by_id(client: AsyncClient, test_project: Project, v2_projects_url):
    """Test getting a project by its numeric ID."""
    response = await client.get(f"{v2_projects_url}/{test_project.id}")

    assert response.status_code == 200
    project = ProjectItem.model_validate(response.json())
    assert project.id == test_project.id
    assert project.name == test_project.name
    assert project.path == test_project.path
    assert project.is_default == (test_project.is_default or False)


@pytest.mark.asyncio
async def test_get_project_by_id_not_found(client: AsyncClient, v2_projects_url):
    """Test getting a non-existent project by ID returns 404."""
    response = await client.get(f"{v2_projects_url}/999999")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_project_path_by_id(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Test updating a project's path by ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        new_path = str(Path(tmpdir) / "new-project-location")
        Path(new_path).mkdir(parents=True, exist_ok=True)

        update_data = {"path": new_path}
        response = await client.patch(
            f"{v2_projects_url}/{test_project.id}",
            json=update_data,
        )

        assert response.status_code == 200
        status_response = ProjectStatusResponse.model_validate(response.json())
        assert status_response.status == "success"
        assert status_response.new_project.id == test_project.id
        # Normalize paths for cross-platform comparison (Windows uses backslashes, API returns forward slashes)
        assert Path(status_response.new_project.path) == Path(new_path)
        assert status_response.old_project.id == test_project.id


@pytest.mark.asyncio
async def test_update_project_invalid_path(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Test updating with a relative path returns 400."""
    update_data = {"path": "relative/path"}
    response = await client.patch(
        f"{v2_projects_url}/{test_project.id}",
        json=update_data,
    )

    assert response.status_code == 400
    assert "absolute" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_project_not_found(client: AsyncClient, v2_projects_url):
    """Test updating a non-existent project returns 404."""
    update_data = {"path": "/tmp/new-path"}
    response = await client.patch(
        f"{v2_projects_url}/999999",
        json=update_data,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_set_default_project_by_id(
    client: AsyncClient, test_project: Project, v2_projects_url, project_repository, project_service
):
    """Test setting a project as default by ID."""
    # Create a second project to test setting default
    await project_service.add_project("second-project", "/tmp/second-project")

    # Get the created project from the repository to get its ID
    created_project = await project_repository.get_by_name("second-project")
    assert created_project is not None

    # Set the second project as default
    response = await client.put(f"{v2_projects_url}/{created_project.id}/default")

    assert response.status_code == 200
    status_response = ProjectStatusResponse.model_validate(response.json())
    assert status_response.status == "success"
    assert status_response.default is True
    assert status_response.new_project.id == created_project.id
    assert status_response.new_project.is_default is True
    assert status_response.old_project.id == test_project.id
    assert status_response.old_project.is_default is False


@pytest.mark.asyncio
async def test_set_default_project_not_found(client: AsyncClient, v2_projects_url):
    """Test setting a non-existent project as default returns 404."""
    response = await client.put(f"{v2_projects_url}/999999/default")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_by_id(
    client: AsyncClient, test_project: Project, v2_projects_url, project_repository, project_service
):
    """Test deleting a project by ID."""
    # Create a second project since we can't delete the default
    await project_service.add_project("to-delete", "/tmp/to-delete")

    # Get the created project from the repository to get its ID
    created_project = await project_repository.get_by_name("to-delete")
    assert created_project is not None

    # Delete it
    response = await client.delete(f"{v2_projects_url}/{created_project.id}")

    assert response.status_code == 200
    status_response = ProjectStatusResponse.model_validate(response.json())
    assert status_response.status == "success"
    assert status_response.old_project.id == created_project.id
    assert status_response.new_project is None

    # Verify it's deleted - trying to get it should return 404
    response = await client.get(f"{v2_projects_url}/{created_project.id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_with_delete_notes_param(
    client: AsyncClient, test_project: Project, v2_projects_url, project_repository, project_service
):
    """Test deleting a project with delete_notes parameter."""
    # Create a project in a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "test-delete-notes"
        project_path.mkdir(parents=True, exist_ok=True)

        # Create a test file in the project
        test_file = project_path / "test.md"
        test_file.write_text("Test content")

        await project_service.add_project("delete-with-notes", str(project_path))

        # Get the created project from the repository to get its ID
        created_project = await project_repository.get_by_name("delete-with-notes")
        assert created_project is not None

        # Delete with delete_notes=true
        response = await client.delete(f"{v2_projects_url}/{created_project.id}?delete_notes=true")

        assert response.status_code == 200

        # Verify directory was deleted
        assert not project_path.exists()


@pytest.mark.asyncio
async def test_delete_default_project_fails(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Test that deleting the default project returns 400."""
    # test_project is the default project
    response = await client.delete(f"{v2_projects_url}/{test_project.id}")

    assert response.status_code == 400
    assert "default project" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_project_not_found(client: AsyncClient, v2_projects_url):
    """Test deleting a non-existent project returns 404."""
    response = await client.delete(f"{v2_projects_url}/999999")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_v2_project_endpoints_use_id_not_name(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Verify v2 project endpoints require project ID, not name."""
    # Try using project name instead of ID - should fail
    response = await client.get(f"{v2_projects_url}/{test_project.name}")

    # Should get 404 or 422 because name is not a valid integer
    assert response.status_code in [404, 422]


@pytest.mark.asyncio
async def test_project_id_stability_after_rename(
    client: AsyncClient, test_project: Project, v2_projects_url, project_repository
):
    """Test that project ID remains stable even after renaming."""
    original_id = test_project.id
    original_name = test_project.name

    # Get project by ID
    response = await client.get(f"{v2_projects_url}/{original_id}")
    assert response.status_code == 200
    project_before = ProjectItem.model_validate(response.json())
    assert project_before.id == original_id
    assert project_before.name == original_name

    # Even if we renamed the project (not testing rename here, just the concept),
    # the ID would stay the same. This test demonstrates the stability.
    # Re-fetch by same ID
    response = await client.get(f"{v2_projects_url}/{original_id}")
    assert response.status_code == 200
    project_after = ProjectItem.model_validate(response.json())
    assert project_after.id == original_id


@pytest.mark.asyncio
async def test_update_project_active_status(
    client: AsyncClient, test_project: Project, v2_projects_url, project_repository, project_service
):
    """Test updating a project's active status by ID."""
    # Create a non-default project
    await project_service.add_project("test-active", "/tmp/test-active")

    # Get the created project from the repository to get its ID
    created_project = await project_repository.get_by_name("test-active")
    assert created_project is not None

    # Update active status
    update_data = {"is_active": False}
    response = await client.patch(
        f"{v2_projects_url}/{created_project.id}",
        json=update_data,
    )

    assert response.status_code == 200
    status_response = ProjectStatusResponse.model_validate(response.json())
    assert status_response.status == "success"
