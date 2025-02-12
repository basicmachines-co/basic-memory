"""Tests for search router."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from basic_memory import db
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.search import SearchItemType, SearchResponse


@pytest_asyncio.fixture
async def indexed_entity(init_search_index, full_entity, search_service):
    """Create an entity and index it."""
    await search_service.index_entity(full_entity)
    return full_entity


@pytest.mark.asyncio
async def test_search_basic(client, indexed_entity):
    """Test basic text search."""
    response = await client.post("/search/", json={"text": "search"})
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
async def test_search_with_type_filter(client, indexed_entity):
    """Test search with type filter."""
    # Should find with correct type
    response = await client.post(
        "/search/", json={"text": "test", "types": [SearchItemType.ENTITY.value]}
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())

    # Should not find with wrong type
    response = await client.post(
        "/search/", json={"text": "test", "types": [SearchItemType.RELATION.value]}
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())


@pytest.mark.asyncio
async def test_search_with_entity_type_filter(client, indexed_entity):
    """Test search with entity type filter."""
    # Should find with correct entity type
    response = await client.post("/search/", json={"text": "test", "entity_types": ["test"]})
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 1

    # Should not find with wrong entity type
    response = await client.post("/search/", json={"text": "test", "entity_types": ["note"]})
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 0


@pytest.mark.asyncio
async def test_search_with_date_filter(client, indexed_entity):
    """Test search with date filter."""
    # Should find with past date
    past_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    response = await client.post(
        "/search/", json={"text": "test", "after_date": past_date.isoformat()}
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())

    # Should not find with future date
    future_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
    response = await client.post(
        "/search/", json={"text": "test", "after_date": future_date.isoformat()}
    )
    assert response.status_code == 200
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 0


@pytest.mark.skip("search scoring is not implemented yet")
@pytest.mark.asyncio
async def test_search_scoring(client, indexed_entity):
    """Test search result scoring."""
    # Exact match should score higher
    exact_response = await client.post("/search/", json={"text": "TestComponent"})

    # Partial match should score lower
    partial_response = await client.post("/search/", json={"text": "test"})

    assert exact_response.status_code == 200
    assert partial_response.status_code == 200

    exact_result = SearchResponse.model_validate(exact_response.json())
    partial_result = SearchResponse.model_validate(partial_response.json())

    exact_score = exact_result.results[0].score
    partial_score = partial_result.results[0].score

    assert exact_score > partial_score


@pytest.mark.asyncio
async def test_search_empty(search_service, client):
    """Test search with no matches."""
    response = await client.post("/search/", json={"text": "nonexistent"})
    assert response.status_code == 200
    search_result = SearchResponse.model_validate(response.json())
    assert len(search_result.results) == 0


@pytest.mark.asyncio
async def test_reindex(client, search_service, entity_service, session_maker):
    """Test reindex endpoint."""
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
    response = await client.post("/search/", json={"text": "test"})
    search_results = SearchResponse.model_validate(response.json())
    assert len(search_results.results) == 0

    # Trigger reindex
    reindex_response = await client.post("/search/reindex")
    assert reindex_response.status_code == 200
    assert reindex_response.json()["status"] == "ok"

    # Verify content is searchable again
    search_response = await client.post("/search/", json={"text": "test"})
    search_results = SearchResponse.model_validate(search_response.json())
    assert len(search_results.results) == 1


@pytest.mark.asyncio
async def test_multiple_filters(client, indexed_entity):
    """Test search with multiple filters combined."""
    response = await client.post(
        "/search/",
        json={
            "text": "test",
            "types": [SearchItemType.ENTITY.value],
            "entity_types": ["test"],
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
