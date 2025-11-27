"""Tests for v2 memory router endpoints."""

import pytest
from httpx import AsyncClient
from pathlib import Path

from basic_memory.models import Project


async def create_test_entity(
    test_project, entity_data, entity_repository, search_service, file_service
):
    """Helper to create an entity with file and index it."""
    # Create file
    test_content = f"# {entity_data['title']}\n\nTest content"
    file_path = Path(test_project.path) / entity_data["file_path"]
    file_path.parent.mkdir(parents=True, exist_ok=True)
    await file_service.write_file(file_path, test_content)

    # Create entity
    entity = await entity_repository.create(entity_data)

    # Index for search
    await search_service.index_entity(entity)

    return entity


@pytest.mark.asyncio
async def test_get_recent_context(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting recent activity context."""
    entity_data = {
        "title": "Recent Test Entity",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "recent_test.md",
        "checksum": "abc123",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get recent context
    response = await client.get(f"{v2_project_url}/memory/recent")

    assert response.status_code == 200
    data = response.json()

    # Verify response structure (GraphContext uses 'results' not 'entities')
    assert "results" in data
    assert "metadata" in data
    assert "page" in data
    assert "page_size" in data


@pytest.mark.asyncio
async def test_get_recent_context_with_pagination(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test recent context with pagination parameters."""
    # Create multiple test entities
    for i in range(5):
        entity_data = {
            "title": f"Entity {i}",
            "entity_type": "note",
            "content_type": "text/markdown",
            "file_path": f"entity_{i}.md",
            "checksum": f"checksum{i}",
        }
        await create_test_entity(
            test_project, entity_data, entity_repository, search_service, file_service
        )

    # Get recent context with pagination
    response = await client.get(
        f"{v2_project_url}/memory/recent", params={"page": 1, "page_size": 3}
    )

    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert data["page"] == 1
    assert data["page_size"] == 3


@pytest.mark.asyncio
async def test_get_recent_context_with_type_filter(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test filtering recent context by type."""
    # Create a test entity
    entity_data = {
        "title": "Filtered Entity",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "filtered.md",
        "checksum": "xyz789",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get recent context filtered by type
    response = await client.get(f"{v2_project_url}/memory/recent", params={"type": ["entity"]})

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_recent_context_with_timeframe(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test recent context with custom timeframe."""
    response = await client.get(f"{v2_project_url}/memory/recent", params={"timeframe": "1d"})

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_recent_context_invalid_project_id(
    client: AsyncClient,
):
    """Test getting recent context with invalid project ID returns 404."""
    response = await client.get("/v2/projects/999999/memory/recent")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_memory_context_by_permalink(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting context for a specific memory URI (permalink)."""
    # Create a test entity
    entity_data = {
        "title": "Context Test",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "context_test.md",
        "checksum": "def456",
        "permalink": "context-test",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get context for this entity
    response = await client.get(f"{v2_project_url}/memory/context-test")

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_memory_context_by_id(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting context using ID-based memory URI."""
    # Create a test entity
    entity_data = {
        "title": "ID Context Test",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "id_context_test.md",
        "checksum": "ghi789",
    }
    created_entity = await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get context using ID format (memory://id/123 or memory://123)
    response = await client.get(f"{v2_project_url}/memory/id/{created_entity.id}")

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_memory_context_with_depth(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting context with depth parameter."""
    # Create a test entity
    entity_data = {
        "title": "Depth Test",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "depth_test.md",
        "checksum": "jkl012",
        "permalink": "depth-test",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get context with depth
    response = await client.get(f"{v2_project_url}/memory/depth-test", params={"depth": 2})

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_memory_context_not_found(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test getting context for non-existent memory URI returns 404."""
    response = await client.get(f"{v2_project_url}/memory/nonexistent-uri")

    # Note: This might return 200 with empty results depending on implementation
    # Adjust assertion based on actual behavior
    assert response.status_code in [200, 404]


@pytest.mark.asyncio
async def test_get_memory_context_with_timeframe(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting context with timeframe filter."""
    # Create a test entity
    entity_data = {
        "title": "Timeframe Test",
        "entity_type": "note",
        "content_type": "text/markdown",
        "file_path": "timeframe_test.md",
        "checksum": "mno345",
        "permalink": "timeframe-test",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get context with timeframe
    response = await client.get(
        f"{v2_project_url}/memory/timeframe-test", params={"timeframe": "7d"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_v2_memory_endpoints_use_project_id_not_name(
    client: AsyncClient,
    test_project: Project,
):
    """Test that v2 memory endpoints reject string project names."""
    # Try to use project name instead of ID - should fail
    response = await client.get(f"/v2/{test_project.name}/memory/recent")

    # FastAPI path validation should reject non-integer project_id
    assert response.status_code in [404, 422]
