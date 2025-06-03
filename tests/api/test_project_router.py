"""Tests for the project router API endpoints."""

import json
from unittest.mock import patch

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
async def test_get_project_info_watch_status(test_graph, client, project_config, project_url):
    """Test that project-info correctly handles watch status."""
    # Create a mock watch status file
    mock_watch_status = {
        "running": True,
        "start_time": "2025-03-05T18:00:42.752435",
        "pid": 7321,
        "error_count": 0,
        "last_error": None,
        "last_scan": "2025-03-05T19:59:02.444416",
        "synced_files": 6,
        "recent_events": [],
    }

    # Mock the Path.exists and Path.read_text methods
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value=json.dumps(mock_watch_status)),
    ):
        # Call the endpoint
        response = await client.get(f"{project_url}/project/info")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check that watch status is included
        assert data["system"]["watch_status"] is not None
        assert data["system"]["watch_status"]["running"] is True
        assert data["system"]["watch_status"]["pid"] == 7321
        assert data["system"]["watch_status"]["synced_files"] == 6


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
