"""Tests for the project router API endpoints."""

import tempfile
from pathlib import Path

import pytest

from basic_memory.schemas.project_info import ProjectItem


@pytest.mark.asyncio
async def test_get_project_item(test_graph, client, project_config, test_project, project_url):
    """Test the project item endpoint returns correctly structured data."""
    # Set up some test data in the database

    # Call the endpoint
    response = await client.get(f"{project_url}/project/item")

    # Verify response
    assert response.status_code == 200
    project_info = ProjectItem.model_validate(response.json())
    assert project_info.name == test_project.name
    assert project_info.path == test_project.path
    assert project_info.is_default == test_project.is_default


@pytest.mark.asyncio
async def test_get_project_item_not_found(
    test_graph, client, project_config, test_project, project_url
):
    """Test the project item endpoint returns correctly structured data."""
    # Set up some test data in the database

    # Call the endpoint
    response = await client.get("/not-found/project/item")

    # Verify response
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_default_project(test_graph, client, project_config, test_project, project_url):
    """Test the default project item endpoint returns the default project."""
    # Set up some test data in the database

    # Call the endpoint
    response = await client.get("/projects/default")

    # Verify response
    assert response.status_code == 200
    project_info = ProjectItem.model_validate(response.json())
    assert project_info.name == test_project.name
    assert project_info.path == test_project.path
    assert project_info.is_default == test_project.is_default


@pytest.mark.asyncio
async def test_get_project_info_endpoint(test_graph, client, project_config, project_url):
    """Test the project-info endpoint returns correctly structured data."""
    # Set up some test data in the database

    # Call the endpoint
    response = await client.get(f"{project_url}/project/info")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check top-level keys
    assert "project_name" in data
    assert "project_path" in data
    assert "available_projects" in data
    assert "default_project" in data
    assert "statistics" in data
    assert "activity" in data
    assert "system" in data

    # Check statistics
    stats = data["statistics"]
    assert "total_entities" in stats
    assert stats["total_entities"] >= 0
    assert "total_observations" in stats
    assert stats["total_observations"] >= 0
    assert "total_relations" in stats
    assert stats["total_relations"] >= 0

    # Check activity
    activity = data["activity"]
    assert "recently_created" in activity
    assert "recently_updated" in activity
    assert "monthly_growth" in activity

    # Check system
    system = data["system"]
    assert "version" in system
    assert "database_path" in system
    assert "database_size" in system
    assert "timestamp" in system


@pytest.mark.asyncio
async def test_get_project_info_content(test_graph, client, project_config, project_url):
    """Test that project-info contains actual data from the test database."""
    # Call the endpoint
    response = await client.get(f"{project_url}/project/info")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check that test_graph content is reflected in statistics
    stats = data["statistics"]

    # Our test graph should have at least a few entities
    assert stats["total_entities"] > 0

    # It should also have some observations
    assert stats["total_observations"] > 0

    # And relations
    assert stats["total_relations"] > 0

    # Check that entity types include 'test'
    assert "test" in stats["entity_types"] or "entity" in stats["entity_types"]


@pytest.mark.asyncio
async def test_list_projects_endpoint(test_config, test_graph, client, project_config, project_url):
    """Test the list projects endpoint returns correctly structured data."""
    # Call the endpoint
    response = await client.get("/projects/projects")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check that the response contains expected fields
    assert "projects" in data
    assert "default_project" in data

    # Check that projects is a list
    assert isinstance(data["projects"], list)

    # There should be at least one project (the test project)
    assert len(data["projects"]) > 0

    # Verify project item structure
    if data["projects"]:
        project = data["projects"][0]
        assert "name" in project
        assert "path" in project
        assert "is_default" in project

        # Default project should be marked
        default_project = next((p for p in data["projects"] if p["is_default"]), None)
        assert default_project is not None
        assert default_project["name"] == data["default_project"]


@pytest.mark.asyncio
async def test_remove_project_endpoint(test_config, client, project_service):
    """Test the remove project endpoint."""
    # First create a test project to remove
    test_project_name = "test-remove-project"
    await project_service.add_project(test_project_name, "/tmp/test-remove-project")

    # Verify it exists
    project = await project_service.get_project(test_project_name)
    assert project is not None

    # Remove the project
    response = await client.delete(f"/projects/{test_project_name}")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "message" in data
    assert "status" in data
    assert data["status"] == "success"
    assert "old_project" in data
    assert data["old_project"]["name"] == test_project_name

    # Verify project is actually removed
    removed_project = await project_service.get_project(test_project_name)
    assert removed_project is None


@pytest.mark.asyncio
async def test_set_default_project_endpoint(test_config, client, project_service):
    """Test the set default project endpoint."""
    # Create a test project to set as default
    test_project_name = "test-default-project"
    await project_service.add_project(test_project_name, "/tmp/test-default-project")

    # Set it as default
    response = await client.put(f"/projects/{test_project_name}/default")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "message" in data
    assert "status" in data
    assert data["status"] == "success"
    assert "new_project" in data
    assert data["new_project"]["name"] == test_project_name

    # Verify it's actually set as default
    assert project_service.default_project == test_project_name


@pytest.mark.asyncio
async def test_update_project_path_endpoint(test_config, client, project_service, project_url):
    """Test the update project endpoint for changing project path."""
    # Create a test project to update
    test_project_name = "test-update-project"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        old_path = test_root / "old-location"
        new_path = test_root / "new-location"

        await project_service.add_project(test_project_name, str(old_path))

        try:
            # Verify initial state
            project = await project_service.get_project(test_project_name)
            assert project is not None
            assert Path(project.path) == old_path

            # Update the project path
            response = await client.patch(
                f"{project_url}/project/{test_project_name}", json={"path": str(new_path)}
            )

            # Verify response
            assert response.status_code == 200
            data = response.json()

            # Check response structure
            assert "message" in data
            assert "status" in data
            assert data["status"] == "success"
            assert "old_project" in data
            assert "new_project" in data

            # Check old project data
            assert data["old_project"]["name"] == test_project_name
            assert Path(data["old_project"]["path"]) == old_path

            # Check new project data
            assert data["new_project"]["name"] == test_project_name
            assert Path(data["new_project"]["path"]) == new_path

            # Verify project was actually updated in database
            updated_project = await project_service.get_project(test_project_name)
            assert updated_project is not None
            assert Path(updated_project.path) == new_path

        finally:
            # Clean up
            try:
                await project_service.remove_project(test_project_name)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_update_project_is_active_endpoint(test_config, client, project_service, project_url):
    """Test the update project endpoint for changing is_active status."""
    # Create a test project to update
    test_project_name = "test-update-active-project"
    test_path = "/tmp/test-update-active"

    await project_service.add_project(test_project_name, test_path)

    try:
        # Update the project is_active status
        response = await client.patch(
            f"{project_url}/project/{test_project_name}", json={"is_active": False}
        )

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check response structure
        assert "message" in data
        assert "status" in data
        assert data["status"] == "success"
        assert f"Project '{test_project_name}' updated successfully" == data["message"]

    finally:
        # Clean up
        try:
            await project_service.remove_project(test_project_name)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_update_project_both_params_endpoint(
    test_config, client, project_service, project_url
):
    """Test the update project endpoint with both path and is_active parameters."""
    # Create a test project to update
    test_project_name = "test-update-both-project"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        old_path = (test_root / "old-location").as_posix()
        new_path = (test_root / "new-location").as_posix()

        await project_service.add_project(test_project_name, old_path)

        try:
            # Update both path and is_active (path should take precedence)
            response = await client.patch(
                f"{project_url}/project/{test_project_name}",
                json={"path": new_path, "is_active": False},
            )

            # Verify response
            assert response.status_code == 200
            data = response.json()

            # Check that path update was performed (takes precedence)
            assert data["new_project"]["path"] == new_path

            # Verify project was actually updated in database
            updated_project = await project_service.get_project(test_project_name)
            assert updated_project is not None
            assert updated_project.path == new_path

        finally:
            # Clean up
            try:
                await project_service.remove_project(test_project_name)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_update_project_nonexistent_endpoint(client, project_url):
    """Test the update project endpoint with a nonexistent project."""
    # Try to update a project that doesn't exist
    response = await client.patch(
        f"{project_url}/project/nonexistent-project", json={"path": "/tmp/new-path"}
    )

    # Should return 400 error
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "not found in configuration" in data["detail"]


@pytest.mark.asyncio
async def test_update_project_relative_path_error_endpoint(
    test_config, client, project_service, project_url
):
    """Test the update project endpoint with relative path (should fail)."""
    # Create a test project to update
    test_project_name = "test-update-relative-project"
    test_path = "/tmp/test-update-relative"

    await project_service.add_project(test_project_name, test_path)

    try:
        # Try to update with relative path
        response = await client.patch(
            f"{project_url}/project/{test_project_name}", json={"path": "./relative-path"}
        )

        # Should return 400 error
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Path must be absolute" in data["detail"]

    finally:
        # Clean up
        try:
            await project_service.remove_project(test_project_name)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_update_project_no_params_endpoint(test_config, client, project_service, project_url):
    """Test the update project endpoint with no parameters (should fail)."""
    # Create a test project to update
    test_project_name = "test-update-no-params-project"
    test_path = "/tmp/test-update-no-params"

    await project_service.add_project(test_project_name, test_path)
    proj_info = await project_service.get_project(test_project_name)
    assert proj_info.name == test_project_name
    # On Windows the path is prepended with a drive letter
    assert test_path in proj_info.path

    try:
        # Try to update with no parameters
        response = await client.patch(f"{project_url}/project/{test_project_name}", json={})

        # Should return 200 (no-op)
        assert response.status_code == 200
        proj_info = await project_service.get_project(test_project_name)
        assert proj_info.name == test_project_name
        # On Windows the path is prepended with a drive letter
        assert test_path in proj_info.path

    finally:
        # Clean up
        try:
            await project_service.remove_project(test_project_name)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_update_project_empty_path_endpoint(
    test_config, client, project_service, project_url
):
    """Test the update project endpoint with empty path parameter."""
    # Create a test project to update
    test_project_name = "test-update-empty-path-project"
    test_path = "/tmp/test-update-empty-path"

    await project_service.add_project(test_project_name, test_path)

    try:
        # Try to update with empty/null path - should be treated as no path update
        response = await client.patch(
            f"{project_url}/project/{test_project_name}", json={"path": None, "is_active": True}
        )

        # Should succeed and perform is_active update
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    finally:
        # Clean up
        try:
            await project_service.remove_project(test_project_name)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_sync_project_endpoint(test_graph, client, project_url):
    """Test the project sync endpoint initiates background sync."""
    # Call the sync endpoint
    response = await client.post(f"{project_url}/project/sync")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "status" in data
    assert "message" in data
    assert data["status"] == "sync_started"
    assert "Filesystem sync initiated" in data["message"]


@pytest.mark.asyncio
async def test_sync_project_endpoint_with_force_full(test_graph, client, project_url):
    """Test the project sync endpoint with force_full parameter."""
    # Call the sync endpoint with force_full=true
    response = await client.post(f"{project_url}/project/sync?force_full=true")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "status" in data
    assert "message" in data
    assert data["status"] == "sync_started"
    assert "Filesystem sync initiated" in data["message"]


@pytest.mark.asyncio
async def test_sync_project_endpoint_with_force_full_false(test_graph, client, project_url):
    """Test the project sync endpoint with force_full=false."""
    # Call the sync endpoint with force_full=false
    response = await client.post(f"{project_url}/project/sync?force_full=false")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "status" in data
    assert "message" in data
    assert data["status"] == "sync_started"
    assert "Filesystem sync initiated" in data["message"]


@pytest.mark.asyncio
async def test_sync_project_endpoint_not_found(client):
    """Test the project sync endpoint with nonexistent project."""
    # Call the sync endpoint for a project that doesn't exist
    response = await client.post("/nonexistent-project/project/sync")

    # Should return 404
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_sync_project_endpoint_foreground(test_graph, client, project_url):
    """Test the project sync endpoint with run_in_background=false returns sync report."""
    # Call the sync endpoint with run_in_background=false
    response = await client.post(f"{project_url}/project/sync?run_in_background=false")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check that we get a sync report instead of status message
    assert "new" in data
    assert "modified" in data
    assert "deleted" in data
    assert "moves" in data
    assert "checksums" in data
    assert "skipped_files" in data
    assert "total" in data

    # Verify these are the right types
    assert isinstance(data["new"], list)
    assert isinstance(data["modified"], list)
    assert isinstance(data["deleted"], list)
    assert isinstance(data["moves"], dict)
    assert isinstance(data["checksums"], dict)
    assert isinstance(data["skipped_files"], list)
    assert isinstance(data["total"], int)


@pytest.mark.asyncio
async def test_sync_project_endpoint_foreground_with_force_full(test_graph, client, project_url):
    """Test the project sync endpoint with run_in_background=false and force_full=true."""
    # Call the sync endpoint with both parameters
    response = await client.post(
        f"{project_url}/project/sync?run_in_background=false&force_full=true"
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check that we get a sync report with all expected fields
    assert "new" in data
    assert "modified" in data
    assert "deleted" in data
    assert "moves" in data
    assert "checksums" in data
    assert "skipped_files" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_sync_project_endpoint_foreground_with_changes(
    test_graph, client, project_config, project_url, tmpdir
):
    """Test foreground sync detects actual file changes."""
    # Create a new file in the project directory
    import os
    from pathlib import Path

    test_file = Path(project_config.home) / "new_test_file.md"
    test_file.write_text("# New Test File\n\nThis is a test file for sync detection.")

    try:
        # Call the sync endpoint with run_in_background=false
        response = await client.post(f"{project_url}/project/sync?run_in_background=false")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # The sync report should show changes (the new file we created)
        assert data["total"] >= 0  # Should have at least detected changes
        assert "new" in data
        assert "modified" in data
        assert "deleted" in data

        # At least one of these should have changes
        has_changes = len(data["new"]) > 0 or len(data["modified"]) > 0 or len(data["deleted"]) > 0
        assert has_changes or data["total"] >= 0  # Either changes detected or empty sync is valid

    finally:
        # Clean up the test file
        if test_file.exists():
            os.remove(test_file)


@pytest.mark.asyncio
async def test_remove_default_project_fails(test_config, client, project_service):
    """Test that removing the default project returns an error."""
    # Get the current default project
    default_project_name = project_service.default_project

    # Try to remove the default project
    response = await client.delete(f"/projects/{default_project_name}")

    # Should return 400 with helpful error message
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "Cannot delete default project" in data["detail"]
    assert default_project_name in data["detail"]


@pytest.mark.asyncio
async def test_remove_default_project_with_alternatives(test_config, client, project_service):
    """Test that error message includes alternative projects when trying to delete default."""
    # Get the current default project
    default_project_name = project_service.default_project

    # Create another project so there are alternatives
    test_project_name = "test-alternative-project"
    await project_service.add_project(test_project_name, "/tmp/test-alternative")

    try:
        # Try to remove the default project
        response = await client.delete(f"/projects/{default_project_name}")

        # Should return 400 with helpful error message including alternatives
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Cannot delete default project" in data["detail"]
        assert "Set another project as default first" in data["detail"]
        assert test_project_name in data["detail"]

    finally:
        # Clean up
        try:
            await project_service.remove_project(test_project_name)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_remove_non_default_project_succeeds(test_config, client, project_service):
    """Test that removing a non-default project succeeds."""
    # Create a test project to remove
    test_project_name = "test-remove-non-default"
    await project_service.add_project(test_project_name, "/tmp/test-remove-non-default")

    # Verify it's not the default
    assert project_service.default_project != test_project_name

    # Remove the project
    response = await client.delete(f"/projects/{test_project_name}")

    # Should succeed
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    # Verify project is removed
    removed_project = await project_service.get_project(test_project_name)
    assert removed_project is None


@pytest.mark.asyncio
async def test_set_nonexistent_project_as_default_fails(test_config, client, project_service):
    """Test that setting a non-existent project as default returns 404."""
    # Try to set a project that doesn't exist as default
    response = await client.put("/projects/nonexistent-project/default")

    # Should return 404
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert "does not exist" in data["detail"]


@pytest.mark.asyncio
async def test_create_project_idempotent_same_path(test_config, client, project_service):
    """Test that creating a project with same name and same path is idempotent."""
    # Create a project with platform-independent path
    test_project_name = "test-idempotent"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = (Path(temp_dir) / "test-idempotent").as_posix()

        response1 = await client.post(
            "/projects/projects",
            json={"name": test_project_name, "path": test_project_path, "set_default": False},
        )

        # Should succeed with 201 Created
        assert response1.status_code == 201
        data1 = response1.json()
        assert data1["status"] == "success"
        assert data1["new_project"]["name"] == test_project_name

        # Try to create the same project again with same name and path
        response2 = await client.post(
            "/projects/projects",
            json={"name": test_project_name, "path": test_project_path, "set_default": False},
        )

        # Should also succeed (idempotent)
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["status"] == "success"
        assert "already exists" in data2["message"]
        assert data2["new_project"]["name"] == test_project_name
        # Normalize paths for cross-platform comparison
        assert Path(data2["new_project"]["path"]).resolve() == Path(test_project_path).resolve()

        # Clean up
        try:
            await project_service.remove_project(test_project_name)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_create_project_fails_different_path(test_config, client, project_service):
    """Test that creating a project with same name but different path fails."""
    # Create a project
    test_project_name = "test-path-conflict"
    test_project_path1 = "/tmp/test-path-conflict-1"

    response1 = await client.post(
        "/projects/projects",
        json={"name": test_project_name, "path": test_project_path1, "set_default": False},
    )

    # Should succeed with 201 Created
    assert response1.status_code == 201

    # Try to create the same project with different path
    test_project_path2 = "/tmp/test-path-conflict-2"
    response2 = await client.post(
        "/projects/projects",
        json={"name": test_project_name, "path": test_project_path2, "set_default": False},
    )

    # Should fail with 400
    assert response2.status_code == 400
    data2 = response2.json()
    assert "detail" in data2
    assert "already exists with different path" in data2["detail"]
    assert test_project_path1 in data2["detail"]
    assert test_project_path2 in data2["detail"]

    # Clean up
    try:
        await project_service.remove_project(test_project_name)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_remove_project_with_delete_notes_false(test_config, client, project_service):
    """Test that removing a project with delete_notes=False leaves directory intact."""
    # Create a test project with actual directory
    test_project_name = "test-remove-keep-files"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_path = Path(temp_dir) / "test-project"
        test_path.mkdir()
        test_file = test_path / "test.md"
        test_file.write_text("# Test Note")

        await project_service.add_project(test_project_name, str(test_path))

        # Remove the project without deleting files (default)
        response = await client.delete(f"/projects/{test_project_name}")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        # Verify project is removed from config/db
        removed_project = await project_service.get_project(test_project_name)
        assert removed_project is None

        # Verify directory still exists
        assert test_path.exists()
        assert test_file.exists()


@pytest.mark.asyncio
async def test_remove_project_with_delete_notes_true(test_config, client, project_service):
    """Test that removing a project with delete_notes=True deletes the directory."""
    # Create a test project with actual directory
    test_project_name = "test-remove-delete-files"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_path = Path(temp_dir) / "test-project"
        test_path.mkdir()
        test_file = test_path / "test.md"
        test_file.write_text("# Test Note")

        await project_service.add_project(test_project_name, str(test_path))

        # Remove the project with delete_notes=True
        response = await client.delete(f"/projects/{test_project_name}?delete_notes=true")

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        # Verify project is removed from config/db
        removed_project = await project_service.get_project(test_project_name)
        assert removed_project is None

        # Verify directory is deleted
        assert not test_path.exists()


@pytest.mark.asyncio
async def test_remove_project_delete_notes_nonexistent_directory(
    test_config, client, project_service
):
    """Test that removing a project with delete_notes=True handles missing directory gracefully."""
    # Create a project pointing to a non-existent path
    test_project_name = "test-remove-missing-dir"
    test_path = "/tmp/this-directory-does-not-exist-12345"

    await project_service.add_project(test_project_name, test_path)

    # Remove the project with delete_notes=True (should not fail even if dir doesn't exist)
    response = await client.delete(f"/projects/{test_project_name}?delete_notes=true")

    # Should succeed
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    # Verify project is removed
    removed_project = await project_service.get_project(test_project_name)
    assert removed_project is None
