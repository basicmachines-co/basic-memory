"""Tests for the project router API endpoints."""

import pytest


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
async def test_update_project_path_endpoint(
    test_config, client, project_service, project_url, tmp_path
):
    """Test the update project endpoint for changing project path."""
    # Create a test project to update
    test_project_name = "test-update-project"
    old_path = (tmp_path / "old-location").as_posix()
    new_path = (tmp_path / "new-location").as_posix()

    await project_service.add_project(test_project_name, old_path)

    try:
        # Verify initial state
        project = await project_service.get_project(test_project_name)
        assert project is not None
        assert project.path == old_path

        # Update the project path
        response = await client.patch(
            f"{project_url}/project/{test_project_name}", json={"path": new_path}
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
        assert data["old_project"]["path"] == old_path

        # Check new project data
        assert data["new_project"]["name"] == test_project_name
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
    test_config, client, project_service, project_url, tmp_path
):
    """Test the update project endpoint with both path and is_active parameters."""
    # Create a test project to update
    test_project_name = "test-update-both-project"
    old_path = (tmp_path / "old-location").as_posix()
    new_path = (tmp_path / "new-location").as_posix()

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
