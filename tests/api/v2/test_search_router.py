"""Tests for v2 search router endpoints."""

import pytest
from httpx import AsyncClient
from pathlib import Path

from basic_memory.deps.services import get_search_service_v2_external
from basic_memory.models import Project
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)


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
async def test_search_entities(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test searching for entities."""
    # Create a test entity
    entity_data = {
        "title": "Searchable Entity",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "searchable.md",
        "checksum": "search123",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Search for the entity
    response = await client.post(f"{v2_project_url}/search/", json={"search_text": "Searchable"})

    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "results" in data
    assert "current_page" in data
    assert "page_size" in data


@pytest.mark.asyncio
async def test_search_with_pagination(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test search with pagination parameters."""
    # Create multiple test entities
    for i in range(5):
        entity_data = {
            "title": f"Search Entity {i}",
            "note_type": "note",
            "content_type": "text/markdown",
            "file_path": f"search_{i}.md",
            "checksum": f"searchsum{i}",
        }
        await create_test_entity(
            test_project, entity_data, entity_repository, search_service, file_service
        )

    # Search with pagination
    response = await client.post(
        f"{v2_project_url}/search/",
        json={"search_text": "Search Entity"},
        params={"page": 1, "page_size": 3},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_page"] == 1
    assert data["page_size"] == 3


@pytest.mark.asyncio
async def test_search_by_permalink(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test searching by permalink."""
    # Create a test entity with permalink
    entity_data = {
        "title": "Permalink Search",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "permalink_search.md",
        "checksum": "perm123",
        "permalink": "permalink-search",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Search by permalink
    response = await client.post(
        f"{v2_project_url}/search/", json={"permalink": "permalink-search"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_search_by_title(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test searching by title."""
    # Create a test entity
    entity_data = {
        "title": "Unique Title For Search",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "unique_title.md",
        "checksum": "title123",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Search by title
    response = await client.post(f"{v2_project_url}/search/", json={"title": "Unique Title"})

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_search_with_type_filter(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test searching with entity type filter."""
    # Create test entities of different types
    for note_type in ["note", "document"]:
        entity_data = {
            "title": f"Type {note_type}",
            "note_type": note_type,
            "content_type": "text/markdown",
            "file_path": f"type_{note_type}.md",
            "checksum": f"type{note_type}",
        }
        await create_test_entity(
            test_project, entity_data, entity_repository, search_service, file_service
        )

    # Search with type filter
    response = await client.post(
        f"{v2_project_url}/search/", json={"search_text": "Type", "note_types": ["note"]}
    )

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_search_with_date_filter(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test searching with date filter."""
    # Create a test entity
    entity_data = {
        "title": "Date Filtered",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "date_filtered.md",
        "checksum": "date123",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Search with date filter
    response = await client.post(
        f"{v2_project_url}/search/",
        json={"search_text": "Date Filtered", "after_date": "2024-01-01T00:00:00Z"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_search_empty_query(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test search with empty query."""
    response = await client.post(f"{v2_project_url}/search/", json={})

    # Empty query should still be valid (returns all)
    assert response.status_code in [200, 422]


@pytest.mark.asyncio
async def test_search_invalid_project_id(
    client: AsyncClient,
):
    """Test searching with invalid project ID returns 404."""
    response = await client.post("/v2/projects/999999/search/", json={"search_text": "test"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reindex(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test reindexing search index."""
    response = await client.post(f"{v2_project_url}/search/reindex")

    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "status" in data
    assert data["status"] == "ok"
    assert "message" in data


@pytest.mark.asyncio
async def test_reindex_invalid_project_id(
    client: AsyncClient,
):
    """Test reindexing with invalid project ID returns 404."""
    response = await client.post("/v2/projects/999999/search/reindex")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_v2_search_endpoints_use_project_id_not_name(
    client: AsyncClient,
    test_project: Project,
):
    """Test that v2 search endpoints reject string project names."""
    # Try to use project name instead of ID - should fail
    response = await client.post(f"/v2/{test_project.name}/search/", json={"search_text": "test"})

    # FastAPI path validation should reject non-integer project_id
    assert response.status_code in [404, 422]


@pytest.mark.asyncio
async def test_search_router_returns_400_for_semantic_disabled(
    client: AsyncClient, app, v2_project_url
):
    """SemanticSearchDisabledError should map to HTTP 400 with detail."""

    class RaisingSearchService:
        async def search(self, *args, **kwargs):
            raise SemanticSearchDisabledError("Semantic search is disabled for this project.")

    app.dependency_overrides[get_search_service_v2_external] = lambda: RaisingSearchService()
    try:
        response = await client.post(
            f"{v2_project_url}/search/",
            json={"search_text": "semantic query", "retrieval_mode": "vector"},
        )
    finally:
        app.dependency_overrides.pop(get_search_service_v2_external, None)

    assert response.status_code == 400
    assert "Semantic search is disabled" in response.json()["detail"]


@pytest.mark.asyncio
async def test_search_router_returns_400_for_semantic_missing_deps(
    client: AsyncClient, app, v2_project_url
):
    """SemanticDependenciesMissingError should map to HTTP 400 with detail."""

    class RaisingSearchService:
        async def search(self, *args, **kwargs):
            raise SemanticDependenciesMissingError("Semantic dependencies are missing.")

    app.dependency_overrides[get_search_service_v2_external] = lambda: RaisingSearchService()
    try:
        response = await client.post(
            f"{v2_project_url}/search/",
            json={"search_text": "semantic query", "retrieval_mode": "hybrid"},
        )
    finally:
        app.dependency_overrides.pop(get_search_service_v2_external, None)

    assert response.status_code == 400
    assert "Semantic dependencies are missing" in response.json()["detail"]


@pytest.mark.asyncio
async def test_search_router_returns_400_for_invalid_vector_query(
    client: AsyncClient, app, v2_project_url
):
    """ValueError from repository validation should map to HTTP 400 with detail."""

    class RaisingSearchService:
        async def search(self, *args, **kwargs):
            raise ValueError("Vector retrieval requires a text query.")

    app.dependency_overrides[get_search_service_v2_external] = lambda: RaisingSearchService()
    try:
        response = await client.post(
            f"{v2_project_url}/search/",
            json={"title": "Root", "retrieval_mode": "vector"},
        )
    finally:
        app.dependency_overrides.pop(get_search_service_v2_external, None)

    assert response.status_code == 400
    assert "Vector retrieval requires a text query" in response.json()["detail"]


@pytest.mark.asyncio
async def test_search_has_more_when_more_results_exist(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """has_more should be True when there are more results beyond the current page."""
    # Create enough entities to exceed a small page_size
    for i in range(4):
        entity_data = {
            "title": f"HasMore Entity {i}",
            "note_type": "note",
            "content_type": "text/markdown",
            "file_path": f"hasmore_{i}.md",
            "checksum": f"hasmore{i}",
        }
        await create_test_entity(
            test_project, entity_data, entity_repository, search_service, file_service
        )

    # Request page_size=2 — with 4 entities, has_more should be True
    response = await client.post(
        f"{v2_project_url}/search/",
        json={"text": "HasMore Entity"},
        params={"page": 1, "page_size": 2},
    )
    assert response.status_code == 200
    data = response.json()
    assert "has_more" in data
    assert data["has_more"] is True
    assert len(data["results"]) == 2


@pytest.mark.asyncio
async def test_search_has_more_false_on_last_page(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """has_more should be False when all results fit on the current page."""
    entity_data = {
        "title": "Solo Search Entity",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "solo_search.md",
        "checksum": "solo123",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    response = await client.post(
        f"{v2_project_url}/search/",
        json={"text": "Solo Search Entity"},
        params={"page": 1, "page_size": 10},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["has_more"] is False
