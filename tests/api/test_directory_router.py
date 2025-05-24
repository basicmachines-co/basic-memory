"""Tests for the directory router API endpoints."""

from unittest.mock import patch

import pytest

from basic_memory.schemas.directory import DirectoryNode


@pytest.mark.asyncio
async def test_get_directory_tree_endpoint(test_graph, client, project_url):
    """Test the get_directory_tree endpoint returns correctly structured data."""
    # Call the endpoint
    response = await client.get(f"{project_url}/directory/tree")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check that the response is a valid directory tree
    assert "name" in data
    assert "directory_path" in data
    assert "children" in data
    assert "type" in data

    # The root node should have children
    assert isinstance(data["children"], list)

    # Root name should be the project name or similar
    assert data["name"]

    # Root directory_path should be a string
    assert isinstance(data["directory_path"], str)


@pytest.mark.asyncio
async def test_get_directory_tree_structure(test_graph, client, project_url):
    """Test the structure of the directory tree returned by the endpoint."""
    # Call the endpoint
    response = await client.get(f"{project_url}/directory/tree")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Function to recursively check each node in the tree
    def check_node_structure(node):
        assert "name" in node
        assert "directory_path" in node
        assert "children" in node
        assert "type" in node
        assert isinstance(node["children"], list)

        # Check each child recursively
        for child in node["children"]:
            check_node_structure(child)

    # Check the entire tree structure
    check_node_structure(data)


@pytest.mark.asyncio
async def test_get_directory_tree_mocked(client, project_url):
    """Test the get_directory_tree endpoint with a mocked service."""
    # Create a mock directory tree
    mock_tree = DirectoryNode(
        name="root",
        directory_path="/test",
        type="directory",
        children=[
            DirectoryNode(
                name="folder1",
                directory_path="/test/folder1",
                type="directory",
                children=[
                    DirectoryNode(
                        name="subfolder",
                        directory_path="/test/folder1/subfolder",
                        type="directory",
                        children=[],
                    )
                ],
            ),
            DirectoryNode(
                name="folder2", directory_path="/test/folder2", type="directory", children=[]
            ),
        ],
    )

    # Patch the directory service
    with patch(
        "basic_memory.services.directory_service.DirectoryService.get_directory_tree",
        return_value=mock_tree,
    ):
        # Call the endpoint
        response = await client.get(f"{project_url}/directory/tree")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check structure matches our mock
        assert data["name"] == "root"
        assert data["directory_path"] == "/test"
        assert data["type"] == "directory"
        assert len(data["children"]) == 2

        # Check first child
        folder1 = data["children"][0]
        assert folder1["name"] == "folder1"
        assert folder1["directory_path"] == "/test/folder1"
        assert folder1["type"] == "directory"
        assert len(folder1["children"]) == 1

        # Check subfolder
        subfolder = folder1["children"][0]
        assert subfolder["name"] == "subfolder"
        assert subfolder["directory_path"] == "/test/folder1/subfolder"
        assert subfolder["type"] == "directory"
        assert subfolder["children"] == []

        # Check second child
        folder2 = data["children"][1]
        assert folder2["name"] == "folder2"
        assert folder2["directory_path"] == "/test/folder2"
        assert folder2["type"] == "directory"
        assert folder2["children"] == []
