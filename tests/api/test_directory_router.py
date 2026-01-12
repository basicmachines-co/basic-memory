"""Tests for the directory router API endpoints."""

import pytest


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
async def test_list_directory_endpoint_default(test_graph, client, project_url):
    """Test the list_directory endpoint with default parameters."""
    # Call the endpoint with default parameters
    response = await client.get(f"{project_url}/directory/list")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Should return a list
    assert isinstance(data, list)

    # With test_graph, should return the "test" directory
    assert len(data) == 1
    assert data[0]["name"] == "test"
    assert data[0]["type"] == "directory"


@pytest.mark.asyncio
async def test_list_directory_endpoint_specific_path(test_graph, client, project_url):
    """Test the list_directory endpoint with specific directory path."""
    # Call the endpoint with /test directory
    response = await client.get(f"{project_url}/directory/list?dir_name=/test")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Should return list of files in test directory
    assert isinstance(data, list)
    assert len(data) == 5

    # All should be files (no subdirectories in test_graph)
    for item in data:
        assert item["type"] == "file"
        assert item["name"].endswith(".md")


@pytest.mark.asyncio
async def test_list_directory_endpoint_with_glob(test_graph, client, project_url):
    """Test the list_directory endpoint with glob filtering."""
    # Call the endpoint with glob filter
    response = await client.get(
        f"{project_url}/directory/list?dir_name=/test&file_name_glob=*Connected*"
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Should return only Connected Entity files
    assert isinstance(data, list)
    assert len(data) == 2

    file_names = {item["name"] for item in data}
    assert file_names == {"Connected Entity 1.md", "Connected Entity 2.md"}


@pytest.mark.asyncio
async def test_list_directory_endpoint_with_depth(test_graph, client, project_url):
    """Test the list_directory endpoint with depth control."""
    # Test depth=1 (default)
    response_depth_1 = await client.get(f"{project_url}/directory/list?dir_name=/&depth=1")
    assert response_depth_1.status_code == 200
    data_depth_1 = response_depth_1.json()
    assert len(data_depth_1) == 1  # Just the test directory

    # Test depth=2 (should include files in test directory)
    response_depth_2 = await client.get(f"{project_url}/directory/list?dir_name=/&depth=2")
    assert response_depth_2.status_code == 200
    data_depth_2 = response_depth_2.json()
    assert len(data_depth_2) == 6  # test directory + 5 files


@pytest.mark.asyncio
async def test_list_directory_endpoint_nonexistent_path(test_graph, client, project_url):
    """Test the list_directory endpoint with nonexistent directory."""
    # Call the endpoint with nonexistent directory
    response = await client.get(f"{project_url}/directory/list?dir_name=/nonexistent")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Should return empty list
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_list_directory_endpoint_validation_errors(client, project_url):
    """Test the list_directory endpoint with invalid parameters."""
    # Test depth too low
    response = await client.get(f"{project_url}/directory/list?depth=0")
    assert response.status_code == 422  # Validation error

    # Test depth too high
    response = await client.get(f"{project_url}/directory/list?depth=11")
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_get_directory_structure_endpoint(test_graph, client, project_url):
    """Test the get_directory_structure endpoint returns folders only."""
    # Call the endpoint
    response = await client.get(f"{project_url}/directory/structure")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check that the response is a valid directory tree
    assert "name" in data
    assert "directory_path" in data
    assert "children" in data
    assert "type" in data
    assert data["type"] == "directory"

    # Root should be present
    assert data["name"] == "Root"
    assert data["directory_path"] == "/"

    # Should have the test directory
    assert len(data["children"]) == 1
    test_dir = data["children"][0]
    assert test_dir["name"] == "test"
    assert test_dir["type"] == "directory"
    assert test_dir["directory_path"] == "/test"

    # Should NOT have any files (test_graph has files but no subdirectories)
    assert len(test_dir["children"]) == 0

    # Verify no file metadata is present in directory nodes
    assert test_dir.get("entity_id") is None
    assert test_dir.get("content_type") is None
    assert test_dir.get("title") is None
    assert test_dir.get("permalink") is None


@pytest.mark.asyncio
async def test_get_directory_structure_empty(client, project_url):
    """Test the get_directory_structure endpoint with empty database."""
    # Call the endpoint
    response = await client.get(f"{project_url}/directory/structure")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Should return root with no children
    assert data["name"] == "Root"
    assert data["directory_path"] == "/"
    assert data["type"] == "directory"
    assert len(data["children"]) == 0
