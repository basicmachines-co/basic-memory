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
async def test_list_directory_endpoint_mocked(client, project_url):
    """Test the list_directory endpoint with mocked service."""
    # Create mock directory nodes
    mock_nodes = [
        DirectoryNode(
            name="folder1",
            directory_path="/folder1",
            type="directory",
        ),
        DirectoryNode(
            name="file1.md",
            directory_path="/file1.md",
            file_path="file1.md",
            type="file",
            title="File 1",
            permalink="file-1",
        ),
    ]

    # Patch the directory service
    with patch(
        "basic_memory.services.directory_service.DirectoryService.list_directory",
        return_value=mock_nodes,
    ):
        # Call the endpoint
        response = await client.get(f"{project_url}/directory/list?dir_name=/test")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check structure matches our mock
        assert isinstance(data, list)
        assert len(data) == 2

        # Check directory
        folder = next(item for item in data if item["type"] == "directory")
        assert folder["name"] == "folder1"
        assert folder["directory_path"] == "/folder1"

        # Check file
        file_item = next(item for item in data if item["type"] == "file")
        assert file_item["name"] == "file1.md"
        assert file_item["directory_path"] == "/file1.md"
        assert file_item["file_path"] == "file1.md"
        assert file_item["title"] == "File 1"
        assert file_item["permalink"] == "file-1"


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


@pytest.mark.asyncio
async def test_get_directory_structure_mocked(client, project_url):
    """Test the get_directory_structure endpoint with mocked service."""
    # Create a mock directory structure (folders only, no files)
    mock_structure = DirectoryNode(
        name="Root",
        directory_path="/",
        type="directory",
        children=[
            DirectoryNode(
                name="docs",
                directory_path="/docs",
                type="directory",
                children=[
                    DirectoryNode(
                        name="guides",
                        directory_path="/docs/guides",
                        type="directory",
                        children=[],
                    ),
                    DirectoryNode(
                        name="api",
                        directory_path="/docs/api",
                        type="directory",
                        children=[],
                    ),
                ],
            ),
            DirectoryNode(name="specs", directory_path="/specs", type="directory", children=[]),
        ],
    )

    # Patch the directory service
    with patch(
        "basic_memory.services.directory_service.DirectoryService.get_directory_structure",
        return_value=mock_structure,
    ):
        # Call the endpoint
        response = await client.get(f"{project_url}/directory/structure")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check structure matches our mock (folders only)
        assert data["name"] == "Root"
        assert data["directory_path"] == "/"
        assert data["type"] == "directory"
        assert len(data["children"]) == 2

        # Check docs directory
        docs = data["children"][0]
        assert docs["name"] == "docs"
        assert docs["directory_path"] == "/docs"
        assert docs["type"] == "directory"
        assert len(docs["children"]) == 2

        # Check subdirectories
        guides = docs["children"][0]
        assert guides["name"] == "guides"
        assert guides["directory_path"] == "/docs/guides"
        assert guides["type"] == "directory"
        assert guides["children"] == []

        api = docs["children"][1]
        assert api["name"] == "api"
        assert api["directory_path"] == "/docs/api"
        assert api["type"] == "directory"
        assert api["children"] == []

        # Check specs directory
        specs = data["children"][1]
        assert specs["name"] == "specs"
        assert specs["directory_path"] == "/specs"
        assert specs["type"] == "directory"
        assert specs["children"] == []
