"""Tests for search service."""

from datetime import datetime

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.schemas.search import SearchQuery, SearchItemType


@pytest.mark.asyncio
async def test_search_permalink(search_service, test_graph):
    """Exact permalink"""
    results = await search_service.search(SearchQuery(permalink="test/root"))
    assert len(results) == 1

    for r in results:
        assert "test/root" in r.permalink


@pytest.mark.asyncio
async def test_search_permalink_observations_wildcard(search_service, test_graph):
    """Pattern matching"""
    results = await search_service.search(SearchQuery(permalink_match="test/root/observations/*"))
    assert len(results) == 2
    permalinks = {r.permalink for r in results}
    assert "test/root/observations/note/root-note-1" in permalinks
    assert "test/root/observations/tech/root-tech-note" in permalinks


@pytest.mark.asyncio
async def test_search_permalink_relation_wildcard(search_service, test_graph):
    """Pattern matching"""
    results = await search_service.search(SearchQuery(permalink_match="test/root/connects_to/*"))
    assert len(results) == 1
    permalinks = {r.permalink for r in results}
    assert "test/root/connects-to/test/connected-entity-1" in permalinks


@pytest.mark.skip("search prefix see:'https://sqlite.org/fts5.html#FTS5 Prefix Queries'")
@pytest.mark.asyncio
async def test_search_permalink_wildcard2(search_service, test_graph):
    """Pattern matching"""
    results = await search_service.search(SearchQuery(permalink_match="test/connected*"))
    assert len(results) == 2
    permalinks = {r.permalink for r in results}
    assert "test/connected1" in permalinks
    assert "test/connected2" in permalinks


@pytest.mark.asyncio
async def test_search_text(search_service, test_graph):
    """Full-text search"""
    results = await search_service.search(
        SearchQuery(text="Root Entity", types=[SearchItemType.ENTITY])
    )
    assert len(results) >= 1
    assert results[0].permalink == "test/root"


@pytest.mark.asyncio
async def test_search_title(search_service, test_graph):
    """Title only search"""
    results = await search_service.search(SearchQuery(title="Root", types=[SearchItemType.ENTITY]))
    assert len(results) >= 1
    assert results[0].permalink == "test/root"


@pytest.mark.asyncio
async def test_text_search_features(search_service, test_graph):
    """Test text search functionality."""
    # Case insensitive
    results = await search_service.search(SearchQuery(text="ENTITY"))
    assert any("test/root" in r.permalink for r in results)

    # Partial word match
    results = await search_service.search(SearchQuery(text="Connect"))
    assert len(results) > 0
    assert any(r.file_path == "test/Connected Entity 2.md" for r in results)

    # Multiple terms
    results = await search_service.search(SearchQuery(text="root note"))
    assert any("test/root" in r.permalink for r in results)


@pytest.mark.asyncio
async def test_pattern_matching(search_service, test_graph):
    """Test pattern matching with various wildcards."""
    # Test wildcards
    results = await search_service.search(SearchQuery(permalink="test/*"))
    for r in results:
        assert "test/" in r.permalink

    # Test start wildcards
    results = await search_service.search(SearchQuery(permalink="*/observations"))
    for r in results:
        assert "/observations" in r.permalink


@pytest.mark.asyncio
async def test_filters(search_service, test_graph):
    """Test search filters."""
    # Combined filters
    results = await search_service.search(
        SearchQuery(text="Deep", types=[SearchItemType.ENTITY], entity_types=["deep"])
    )
    assert len(results) == 1
    for r in results:
        assert r.type == SearchItemType.ENTITY
        assert r.metadata.get("entity_type") == "deep"


@pytest.mark.asyncio
async def test_after_date(search_service, test_graph):
    """Test search filters."""

    # Should find with past date
    past_date = datetime(2020, 1, 1)
    results = await search_service.search(
        SearchQuery(
            text="entity",
            after_date=past_date.isoformat(),
        )
    )
    for r in results:
        assert datetime.fromisoformat(r.created_at) > past_date

    # Should not find with future date
    future_date = datetime(2030, 1, 1)
    results = await search_service.search(
        SearchQuery(
            text="entity",
            after_date=future_date.isoformat(),
        )
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_type(search_service, test_graph):
    """Test search filters."""

    # Should find only type
    results = await search_service.search(SearchQuery(types=[SearchItemType.ENTITY]))
    assert len(results) > 0
    for r in results:
        assert r.type == SearchItemType.ENTITY

    # Should find only types passed in
    results = await search_service.search(SearchQuery(types=[SearchItemType.ENTITY]))
    assert len(results) > 0
    for r in results:
        assert r.type == SearchItemType.ENTITY


@pytest.mark.asyncio
async def test_no_criteria(search_service, test_graph):
    """Test search with no criteria returns empty list."""
    results = await search_service.search(SearchQuery())
    assert len(results) == 0


@pytest.mark.asyncio
async def test_init_search_index(search_service, session_maker):
    """Test search index initialization."""
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='search_index';")
        )
        assert result.scalar() == "search_index"


@pytest.mark.asyncio
async def test_update_index(search_service, full_entity):
    """Test updating indexed content."""
    await search_service.index_entity(full_entity)

    # Update entity
    full_entity.title = "OMG I AM UPDATED"
    await search_service.index_entity(full_entity)

    # Search for new title
    results = await search_service.search(SearchQuery(text="OMG I AM UPDATED"))
    assert len(results) > 1
