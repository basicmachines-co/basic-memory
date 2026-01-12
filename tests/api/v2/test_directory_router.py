"""Tests for V2 directory API routes (ID-based endpoints)."""

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory.schemas.directory import DirectoryNode


@pytest.mark.asyncio
async def test_get_directory_tree(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test getting directory tree via v2 endpoint."""
    response = await client.get(f"{v2_project_url}/directory/tree")

    assert response.status_code == 200
    tree = DirectoryNode.model_validate(response.json())
    assert tree.type == "directory"


@pytest.mark.asyncio
async def test_get_directory_structure(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test getting directory structure (folders only) via v2 endpoint."""
    response = await client.get(f"{v2_project_url}/directory/structure")

    assert response.status_code == 200
    structure = DirectoryNode.model_validate(response.json())
    assert structure.type == "directory"
    # Structure should only contain directories, not files
    if structure.children:
        for child in structure.children:
            assert child.type == "directory"


@pytest.mark.asyncio
async def test_list_directory_default(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test listing directory contents with default parameters via v2 endpoint."""
    response = await client.get(f"{v2_project_url}/directory/list")

    assert response.status_code == 200
    nodes = response.json()
    assert isinstance(nodes, list)


@pytest.mark.asyncio
async def test_list_directory_with_depth(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test listing directory with custom depth via v2 endpoint."""
    response = await client.get(f"{v2_project_url}/directory/list?depth=2")

    assert response.status_code == 200
    nodes = response.json()
    assert isinstance(nodes, list)


@pytest.mark.asyncio
async def test_list_directory_with_glob(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test listing directory with file name glob filter via v2 endpoint."""
    response = await client.get(f"{v2_project_url}/directory/list?file_name_glob=*.md")

    assert response.status_code == 200
    nodes = response.json()
    assert isinstance(nodes, list)
    # All file nodes should have .md extension
    for node in nodes:
        if node.get("type") == "file":
            assert node.get("path", "").endswith(".md")


@pytest.mark.asyncio
async def test_list_directory_with_custom_path(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test listing a specific directory path via v2 endpoint."""
    response = await client.get(f"{v2_project_url}/directory/list?dir_name=/")

    assert response.status_code == 200
    nodes = response.json()
    assert isinstance(nodes, list)


@pytest.mark.asyncio
async def test_directory_invalid_project_id(
    client: AsyncClient,
):
    """Test directory endpoints with invalid project ID return 404."""
    # Test tree endpoint
    response = await client.get("/v2/projects/999999/directory/tree")
    assert response.status_code == 404

    # Test structure endpoint
    response = await client.get("/v2/projects/999999/directory/structure")
    assert response.status_code == 404

    # Test list endpoint
    response = await client.get("/v2/projects/999999/directory/list")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_v2_directory_endpoints_use_project_id_not_name(
    client: AsyncClient, test_project: Project
):
    """Verify v2 directory endpoints require project ID, not name."""
    # Try using project name instead of ID - should fail
    response = await client.get(f"/v2/projects/{test_project.name}/directory/tree")

    # Should get validation error or 404 because name is not a valid integer
    assert response.status_code in [404, 422]
