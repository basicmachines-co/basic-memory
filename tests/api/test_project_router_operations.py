"""Tests for project router operation endpoints."""

import pytest


@pytest.mark.asyncio
async def test_get_project_info_additional(client, test_graph, project_url):
    """Test additional fields in the project info endpoint."""
    # Call the endpoint
    response = await client.get(f"{project_url}/project/info")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check specific fields we're interested in
    assert "available_projects" in data
    assert isinstance(data["available_projects"], dict)

    # Get a project from the list
    for project_name, project_info in data["available_projects"].items():
        # Verify project structure
        assert "path" in project_info
        assert "active" in project_info
        assert "is_default" in project_info
        break  # Just check the first one for structure


@pytest.mark.asyncio
async def test_project_list_additional(client, project_url):
    """Test additional fields in the project list endpoint."""
    # Call the endpoint
    response = await client.get("/projects/projects")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Verify projects list structure in more detail
    assert "projects" in data
    assert len(data["projects"]) > 0

    # Verify the default project is identified
    default_project = data["default_project"]
    assert default_project

    # Verify the default_project appears in the projects list and is marked as default
    default_in_list = False
    for project in data["projects"]:
        if project["name"] == default_project:
            assert project["is_default"] is True
            default_in_list = True
            break

    assert default_in_list, "Default project should appear in the projects list"
