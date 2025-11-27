"""Tests for V2 resource API routes (ID-based endpoints)."""

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory.schemas.v2.resource import ResourceResponse


@pytest.mark.asyncio
async def test_create_resource(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test creating a new resource via v2 POST endpoint."""
    create_data = {
        "file_path": "test-resources/test-file.md",
        "content": "# Test Resource\n\nThis is test content.",
    }

    response = await client.post(
        f"{v2_project_url}/resource",
        json=create_data,
    )

    assert response.status_code == 200
    result = ResourceResponse.model_validate(response.json())

    # V2 must return entity_id
    assert result.entity_id is not None
    assert isinstance(result.entity_id, int)
    assert result.file_path == "test-resources/test-file.md"
    assert result.checksum is not None


@pytest.mark.asyncio
async def test_create_resource_duplicate_fails(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test that creating a resource at an existing path returns 409."""
    create_data = {
        "file_path": "duplicate-test.md",
        "content": "First version",
    }

    # Create first time - should succeed
    response = await client.post(f"{v2_project_url}/resource", json=create_data)
    assert response.status_code == 200

    # Try to create again - should fail with 409
    response = await client.post(f"{v2_project_url}/resource", json=create_data)
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_resource_by_id(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test getting resource content by entity ID."""
    # First create a resource
    test_content = "# Test Resource\n\nThis is test content."
    create_data = {
        "file_path": "test-get.md",
        "content": test_content,
    }

    create_response = await client.post(f"{v2_project_url}/resource", json=create_data)
    assert create_response.status_code == 200
    created = ResourceResponse.model_validate(create_response.json())

    # Now get it by entity ID
    response = await client.get(f"{v2_project_url}/resource/{created.entity_id}")

    assert response.status_code == 200
    # Normalize line endings for cross-platform compatibility
    assert test_content.replace("\n", "") in response.text.replace("\r\n", "").replace("\n", "")


@pytest.mark.asyncio
async def test_get_resource_not_found(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test getting a non-existent resource returns 404."""
    response = await client.get(f"{v2_project_url}/resource/999999")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_resource(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test updating resource content by entity ID."""
    # Create a resource
    create_data = {
        "file_path": "test-update.md",
        "content": "Original content",
    }
    create_response = await client.post(f"{v2_project_url}/resource", json=create_data)
    assert create_response.status_code == 200
    created = ResourceResponse.model_validate(create_response.json())

    # Update it
    update_data = {
        "content": "Updated content",
    }
    response = await client.put(
        f"{v2_project_url}/resource/{created.entity_id}",
        json=update_data,
    )

    assert response.status_code == 200
    result = ResourceResponse.model_validate(response.json())
    assert result.entity_id == created.entity_id
    assert result.file_path == "test-update.md"

    # Verify content was updated
    get_response = await client.get(f"{v2_project_url}/resource/{created.entity_id}")
    assert "Updated content" in get_response.text


@pytest.mark.asyncio
async def test_update_resource_and_move(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test updating resource content and moving it to a new path."""
    # Create a resource
    create_data = {
        "file_path": "original-location.md",
        "content": "Original content",
    }
    create_response = await client.post(f"{v2_project_url}/resource", json=create_data)
    assert create_response.status_code == 200
    created = ResourceResponse.model_validate(create_response.json())

    # Update content and move file
    update_data = {
        "content": "Updated content in new location",
        "file_path": "moved/new-location.md",
    }
    response = await client.put(
        f"{v2_project_url}/resource/{created.entity_id}",
        json=update_data,
    )

    assert response.status_code == 200
    result = ResourceResponse.model_validate(response.json())
    assert result.entity_id == created.entity_id
    assert result.file_path == "moved/new-location.md"

    # Verify content at new location
    get_response = await client.get(f"{v2_project_url}/resource/{created.entity_id}")
    assert "Updated content in new location" in get_response.text


@pytest.mark.asyncio
async def test_update_resource_not_found(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test updating a non-existent resource returns 404."""
    update_data = {
        "content": "New content",
    }
    response = await client.put(
        f"{v2_project_url}/resource/999999",
        json=update_data,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_resource_invalid_path(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test creating a resource with path traversal attempt fails."""
    create_data = {
        "file_path": "../../../etc/passwd",
        "content": "malicious content",
    }

    response = await client.post(f"{v2_project_url}/resource", json=create_data)

    assert response.status_code == 400
    assert "Invalid file path" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_resource_invalid_path(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test updating a resource with path traversal attempt fails."""
    # Create a valid resource first
    create_data = {
        "file_path": "valid.md",
        "content": "Valid content",
    }
    create_response = await client.post(f"{v2_project_url}/resource", json=create_data)
    assert create_response.status_code == 200
    created = ResourceResponse.model_validate(create_response.json())

    # Try to move it to an invalid path
    update_data = {
        "content": "Updated content",
        "file_path": "../../../etc/passwd",
    }
    response = await client.put(
        f"{v2_project_url}/resource/{created.entity_id}",
        json=update_data,
    )

    assert response.status_code == 400
    assert "Invalid file path" in response.json()["detail"]


@pytest.mark.asyncio
async def test_resource_invalid_project_id(
    client: AsyncClient,
):
    """Test resource endpoints with invalid project ID return 404."""
    # Test create
    response = await client.post(
        "/v2/projects/999999/resource",
        json={"file_path": "test.md", "content": "test"},
    )
    assert response.status_code == 404

    # Test get
    response = await client.get("/v2/projects/999999/resource/1")
    assert response.status_code == 404

    # Test update
    response = await client.put(
        "/v2/projects/999999/resource/1",
        json={"content": "test"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_v2_resource_endpoints_use_project_id_not_name(
    client: AsyncClient, test_project: Project
):
    """Verify v2 resource endpoints require project ID, not name."""
    # Try using project name instead of ID - should fail
    response = await client.get(f"/v2/projects/{test_project.name}/resource/1")

    # Should get validation error or 404 because name is not a valid integer
    assert response.status_code in [404, 422]
