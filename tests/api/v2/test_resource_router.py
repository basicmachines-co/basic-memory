"""Tests for v2 resource router endpoints."""

import pytest
from httpx import AsyncClient
from pathlib import Path

from basic_memory.models import Entity, Project


@pytest.mark.asyncio
async def test_get_resource_by_id(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    file_service,
):
    """Test getting resource content by entity ID."""
    # Create a test file
    test_content = "# Test Resource\n\nThis is test content."
    file_path = Path(test_project.path) / "test_resource.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    await file_service.write_file(file_path, test_content)

    # Create entity record
    entity_data = {
        "title": "Test Resource",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "test_resource.md",
        "checksum": "res123",
        "project_id": test_project.id,
    }
    created_entity = await entity_repository.create(entity_data)

    # Get resource by ID
    response = await client.get(f"{v2_project_url}/resource/{created_entity.id}")

    assert response.status_code == 200
    assert test_content in response.text


@pytest.mark.asyncio
async def test_get_resource_by_permalink(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    file_service,
):
    """Test getting resource content by permalink."""
    # Create a test file
    test_content = "# Permalink Resource\n\nContent with permalink."
    file_path = Path(test_project.path) / "permalink_resource.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    await file_service.write_file(file_path, test_content)

    # Create entity with permalink
    entity_data = {
        "title": "Permalink Resource",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "permalink_resource.md",
        "checksum": "perm456",
        "permalink": "permalink-resource",
    }
    await entity_repository.create(entity_data)

    # Get resource by permalink
    response = await client.get(f"{v2_project_url}/resource/permalink-resource")

    assert response.status_code == 200
    assert test_content in response.text


@pytest.mark.asyncio
async def test_get_resource_with_wildcard(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    file_service,
    search_service,
):
    """Test getting resources using wildcard pattern."""
    # Create multiple test files
    for i in range(3):
        test_content = f"# Wildcard Resource {i}\n\nContent {i}."
        file_path = Path(test_project.path) / f"wildcard_{i}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        await file_service.write_file(file_path, test_content)

        entity_data = {
            "title": f"Wildcard Resource {i}",
            "entity_type": "note",
        "content_type": "text/markdown",
            "file_path": f"wildcard_{i}.md",
            "checksum": f"wild{i}",
            "permalink": f"wildcard-{i}",
        }
        entity = await entity_repository.create(entity_data)
        await search_service.index_entity(entity)

    # Get resources with wildcard
    response = await client.get(f"{v2_project_url}/resource/wildcard-*")

    assert response.status_code == 200
    # Response should contain multiple resources concatenated
    assert "Wildcard Resource" in response.text


@pytest.mark.asyncio
async def test_get_resource_not_found(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test getting non-existent resource returns 404."""
    response = await client.get(f"{v2_project_url}/resource/nonexistent")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_resource_file_not_found(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
):
    """Test getting resource when entity exists but file doesn't."""
    # Create entity without actual file
    entity_data = {
        "title": "Missing File",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "missing_file.md",
        "checksum": "miss123",
        "permalink": "missing-file",
    }
    await entity_repository.create(entity_data)

    # Try to get resource
    response = await client.get(f"{v2_project_url}/resource/missing-file")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_resource_invalid_project_id(
    client: AsyncClient,
):
    """Test getting resource with invalid project ID returns 404."""
    response = await client.get("/v2/999999/resource/test")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_write_resource_new_file(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
):
    """Test writing a new resource file."""
    test_content = "# New Resource\n\nThis is new content."

    response = await client.put(
        f"{v2_project_url}/resource/new_resource.md",
        content=test_content,
        headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 201
    data = response.json()

    # Verify response
    assert "file_path" in data
    assert data["file_path"] == "new_resource.md"
    assert "checksum" in data
    assert "size" in data

    # Verify entity was created
    entity = await entity_repository.get_by_file_path("new_resource.md")
    assert entity is not None
    assert entity.title == "new_resource.md"


@pytest.mark.asyncio
async def test_write_resource_update_existing(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    file_service,
):
    """Test updating an existing resource file."""
    # Create initial file
    initial_content = "# Initial Content"
    file_path = Path(test_project.path) / "update_resource.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    await file_service.write_file(file_path, initial_content)

    # Create entity
    entity_data = {
        "title": "update_resource.md",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "update_resource.md",
        "checksum": "init123",
    }
    await entity_repository.create(entity_data)

    # Update the file
    updated_content = "# Updated Content\n\nThis is updated."
    response = await client.put(
        f"{v2_project_url}/resource/update_resource.md",
        content=updated_content,
        headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["file_path"] == "update_resource.md"

    # Verify file was updated
    updated_entity = await entity_repository.get_by_file_path("update_resource.md")
    assert updated_entity is not None


@pytest.mark.asyncio
async def test_write_resource_with_subdirectory(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test writing resource in a subdirectory."""
    test_content = "# Nested Resource"

    response = await client.put(
        f"{v2_project_url}/resource/subdir/nested_resource.md",
        content=test_content,
        headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 201
    data = response.json()
    assert data["file_path"] == "subdir/nested_resource.md"

    # Verify directory was created
    nested_file = Path(test_project.path) / "subdir" / "nested_resource.md"
    assert nested_file.exists()


@pytest.mark.asyncio
async def test_write_resource_invalid_project_id(
    client: AsyncClient,
):
    """Test writing resource with invalid project ID returns 404."""
    response = await client.put(
        "/v2/999999/resource/test.md",
        content="Test content",
        headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_write_resource_dict_content_fails(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test that writing dict content returns error."""
    # Try to send JSON object instead of string
    response = await client.put(
        f"{v2_project_url}/resource/test.md",
        json={"content": "test"}  # This sends a dict, not a string
    )

    # Should fail with validation error (422 is FastAPI's validation error code)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_v2_resource_endpoints_use_project_id_not_name(
    client: AsyncClient,
    test_project: Project,
):
    """Test that v2 resource endpoints reject string project names."""
    # Try to use project name instead of ID - should fail
    response = await client.get(f"/v2/{test_project.name}/resource/test")

    # FastAPI path validation should reject non-integer project_id
    assert response.status_code in [404, 422]
