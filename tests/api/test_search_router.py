"""Tests for search router."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from basic_memory import db
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.search import SearchItemType, SearchResponse


@pytest_asyncio.fixture
async def indexed_entity(full_entity, search_service):
    """Create an entity and index it."""
    await search_service.index_entity(full_entity)
    return full_entity


@pytest.mark.asyncio
async def test_search_basic(client, indexed_entity, project_url):
    """Test basic text search."""
    response = await client.post(f"{project_url}/search/", json={"text": "search"})
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 3

    found = False
    for r in search_results.results:
        if r.type == SearchItemType.ENTITY.value:
            assert r.permalink == indexed_entity.permalink
            found = True

    assert found, "Expected to find indexed entity in results"


@pytest.mark.asyncio
async def test_search_basic_pagination(client, indexed_entity, project_url):
    """Test basic text search."""
    response = await client.post(
        f"{project_url}/search/?page=3&page_size=1", json={"text": "search"}
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 1

    assert search_results.current_page == 3
    assert search_results.page_size == 1


@pytest.mark.asyncio
async def test_search_with_entity_type_filter(client, indexed_entity, project_url):
    """Test search with type filter."""
    # Should find with correct type
    response = await client.post(
        f"{project_url}/search/",
        json={"text": "test", "entity_types": [SearchItemType.ENTITY.value]},
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) > 0

    # Should find with relation type
    response = await client.post(
        f"{project_url}/search/",
        json={"text": "test", "entity_types": [SearchItemType.RELATION.value]},
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 2


@pytest.mark.asyncio
async def test_search_with_type_filter(client, indexed_entity, project_url):
    """Test search with entity type filter."""
    # Should find with correct entity type
    response = await client.post(f"{project_url}/search/", json={"text": "test", "types": ["test"]})
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 1

    # Should not find with wrong entity type
    response = await client.post(f"{project_url}/search/", json={"text": "test", "types": ["note"]})
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 0


@pytest.mark.asyncio
async def test_search_with_date_filter(client, indexed_entity, project_url):
    """Test search with date filter."""
    # Should find with past date
    past_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    response = await client.post(
        f"{project_url}/search/", json={"text": "test", "after_date": past_date.isoformat()}
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())

    # Should not find with future date
    future_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
    response = await client.post(
        f"{project_url}/search/", json={"text": "test", "after_date": future_date.isoformat()}
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 0


@pytest.mark.asyncio
async def test_search_empty(search_service, client, project_url):
    """Test search with no matches."""
    response = await client.post(f"{project_url}/search/", json={"text": "nonexistent"})
    assert response.status_code == 200
    search_result = SearchResponse.model_validate(response.json())
    assert len(search_result.results) == 0


@pytest.mark.asyncio
async def test_reindex(
    client, search_service, entity_service, session_maker, project_url, app_config
):
    """Test reindex endpoint."""
    # Skip for Postgres - needs investigation of database connection isolation
    from basic_memory.config import DatabaseBackend

    if app_config.database_backend == DatabaseBackend.POSTGRES:
        pytest.skip("Not yet supported for Postgres - database connection isolation issue")

    # Create test entity and document
    await entity_service.create_entity(
        EntitySchema(
            title="TestEntity1",
            folder="test",
            entity_type="test",
        ),
    )

    # Clear search index
    async with db.scoped_session(session_maker) as session:
        await session.execute(text("DELETE FROM search_index"))
        await session.commit()

    # Verify nothing is searchable
    response = await client.post(f"{project_url}/search/", json={"text": "test"})
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 0

    # Trigger reindex
    reindex_response = await client.post(f"{project_url}/search/reindex")
    assert reindex_response.status_code == 200
    assert reindex_response.json()["status"] == "ok"

    # Verify content is searchable again
    search_response = await client.post(f"{project_url}/search/", json={"text": "test"})
    search_results = SearchResponse.model_validate(search_response.json())
    assert len(search_results.results) == 1


@pytest.mark.asyncio
async def test_multiple_filters(client, indexed_entity, project_url):
    """Test search with multiple filters combined."""
    response = await client.post(
        f"{project_url}/search/",
        json={
            "text": "test",
            "entity_types": [SearchItemType.ENTITY.value],
            "types": ["test"],
            "after_date": datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 200
    search_result = SearchResponse.model_validate(response.json())
    assert len(search_result.results) == 1
    result = search_result.results[0]
    assert result.permalink == indexed_entity.permalink
    assert result.type == SearchItemType.ENTITY.value
    assert result.metadata["entity_type"] == "test"
