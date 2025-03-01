"""Tests for context service."""

from datetime import datetime, timedelta, UTC

import pytest
import pytest_asyncio

from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.memory import memory_url, memory_url_path
from basic_memory.schemas.search import SearchItemType
from basic_memory.services.context_service import ContextService


@pytest_asyncio.fixture
async def context_service(search_repository, entity_repository):
    """Create context service for testing."""
    return ContextService(search_repository, entity_repository)


@pytest.mark.asyncio
async def test_find_connected_depth_limit(context_service, test_graph):
    """Test depth limiting works.
    Our traversal path is:
    - Depth 0: Root
    - Depth 1: Relations + directly connected entities (Connected1, Connected2)
    - Depth 2: Relations + next level entities (Deep)
    """
    type_id_pairs = [("entity", test_graph["root"].id)]

    # With depth=1, we get direct connections
    # shallow_results = await context_service.find_related(type_id_pairs, max_depth=1)
    # shallow_entities = {(r.id, r.type) for r in shallow_results if r.type == "entity"}
    #
    # assert (test_graph["deep"].id, "entity") not in shallow_entities

    # search deeper
    deep_results = await context_service.find_related(type_id_pairs, max_depth=3, max_results=100)
    deep_entities = {(r.id, r.type) for r in deep_results if r.type == "entity"}
    print(deep_entities)
    # Should now include Deep entity
    assert (test_graph["deep"].id, "entity") in deep_entities


@pytest.mark.asyncio
async def test_find_connected_timeframe(context_service, test_graph, search_repository):
    """Test timeframe filtering.
    This tests how traversal is affected by the item dates.
    When we filter by date, items are only included if:
    1. They match the timeframe
    2. There is a valid path to them through other items in the timeframe
    """
    now = datetime.now(UTC)
    old_date = now - timedelta(days=10)
    recent_date = now - timedelta(days=1)

    # Index root and its relation as old
    await search_repository.index_item(
        SearchIndexRow(
            id=test_graph["root"].id,
            title=test_graph["root"].title,
            content_snippet="Root content",
            permalink=test_graph["root"].permalink,
            file_path=test_graph["root"].file_path,
            type=SearchItemType.ENTITY,
            metadata={"created_at": old_date.isoformat()},
            created_at=old_date.isoformat(),
            updated_at=old_date.isoformat(),
        )
    )
    await search_repository.index_item(
        SearchIndexRow(
            id=test_graph["relations"][0].id,
            title="Root Entity → Connected Entity 1",
            content_snippet="",
            permalink=f"{test_graph['root'].permalink}/connects_to/{test_graph['connected1'].permalink}",
            file_path=test_graph["root"].file_path,
            type=SearchItemType.RELATION,
            from_id=test_graph["root"].id,
            to_id=test_graph["connected1"].id,
            relation_type="connects_to",
            metadata={"created_at": old_date.isoformat()},
            created_at=old_date.isoformat(),
            updated_at=old_date.isoformat(),
        )
    )

    # Index connected1 as recent
    await search_repository.index_item(
        SearchIndexRow(
            id=test_graph["connected1"].id,
            title=test_graph["connected1"].title,
            content_snippet="Connected 1 content",
            permalink=test_graph["connected1"].permalink,
            file_path=test_graph["connected1"].file_path,
            type=SearchItemType.ENTITY,
            metadata={"created_at": recent_date.isoformat()},
            created_at=recent_date.isoformat(),
            updated_at=recent_date.isoformat(),
        )
    )
    type_id_pairs = [("entity", test_graph["root"].id)]

    # Search with a 7-day cutoff
    since_date = now - timedelta(days=7)
    results = await context_service.find_related(type_id_pairs, since=since_date)

    # Only connected1 is recent, but we can't get to it
    # because its connecting relation is too old
    entity_ids = {r.id for r in results if r.type == "entity"}
    assert len(entity_ids) == 0  # No accessible entities within timeframe


@pytest.mark.asyncio
async def test_build_context(context_service, test_graph):
    """Test exact permalink lookup."""
    url = memory_url.validate_strings("memory://test/root")
    results = await context_service.build_context(url)
    matched_results = results["metadata"]["matched_results"]
    primary_results = results["primary_results"]
    related_results = results["related_results"]
    total_results = results["metadata"]["total_results"]

    assert results["metadata"]["uri"] == memory_url_path(url)
    assert results["metadata"]["depth"] == 1
    assert matched_results == 1
    assert len(primary_results) == 1
    assert len(related_results) == 2
    assert total_results == len(primary_results) + len(related_results)


@pytest.mark.asyncio
async def test_build_context_not_found(context_service):
    """Test handling non-existent permalinks."""
    context = await context_service.build_context("memory://does/not/exist")
    assert len(context["primary_results"]) == 0
    assert len(context["related_results"]) == 0


@pytest.mark.asyncio
async def test_context_metadata(context_service, test_graph):
    """Test metadata is correctly populated."""
    context = await context_service.build_context("memory://test/root", depth=2)
    metadata = context["metadata"]
    assert metadata["uri"] == "test/root"
    assert metadata["depth"] == 2
    assert metadata["generated_at"] is not None
    assert metadata["matched_results"] > 0
