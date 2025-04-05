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
async def test_search_limit_offset(search_service, test_graph):
    """Exact permalink"""
    results = await search_service.search(SearchQuery(permalink_match="test/*"))
    assert len(results) > 1

    results = await search_service.search(SearchQuery(permalink_match="test/*"), limit=1)
    assert len(results) == 1

    results = await search_service.search(SearchQuery(permalink_match="test/*"), limit=100)
    num_results = len(results)

    # assert offset
    offset_results = await search_service.search(
        SearchQuery(permalink_match="test/*"), limit=100, offset=1
    )
    assert len(offset_results) == num_results - 1


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
    results = await search_service.search(SearchQuery(permalink_match="test/root/connects-to/*"))
    assert len(results) == 1
    permalinks = {r.permalink for r in results}
    assert "test/root/connects-to/test/connected-entity-1" in permalinks


@pytest.mark.asyncio
async def test_search_permalink_wildcard2(search_service, test_graph):
    """Pattern matching"""
    results = await search_service.search(
        SearchQuery(
            permalink_match="test/connected*",
        )
    )
    assert len(results) >= 2
    permalinks = {r.permalink for r in results}
    assert "test/connected-entity-1" in permalinks
    assert "test/connected-entity-2" in permalinks


@pytest.mark.asyncio
async def test_search_text(search_service, test_graph):
    """Full-text search"""
    results = await search_service.search(
        SearchQuery(text="Root Entity", entity_types=[SearchItemType.ENTITY])
    )
    assert len(results) >= 1
    assert results[0].permalink == "test/root"


@pytest.mark.asyncio
async def test_search_title(search_service, test_graph):
    """Title only search"""
    results = await search_service.search(
        SearchQuery(title="Root", entity_types=[SearchItemType.ENTITY])
    )
    assert len(results) >= 1
    assert results[0].permalink == "test/root"


@pytest.mark.asyncio
async def test_text_search_case_insensitive(search_service, test_graph):
    """Test text search functionality."""
    # Case insensitive
    results = await search_service.search(SearchQuery(text="ENTITY"))
    assert any("test/root" in r.permalink for r in results)


@pytest.mark.asyncio
async def test_text_search_content_word_match(search_service, test_graph):
    """Test text search functionality."""

    # content word match
    results = await search_service.search(SearchQuery(text="Connected"))
    assert len(results) > 0
    assert any(r.file_path == "test/Connected Entity 2.md" for r in results)


@pytest.mark.asyncio
async def test_text_search_multiple_terms(search_service, test_graph):
    """Test text search functionality."""

    # Multiple terms
    results = await search_service.search(SearchQuery(text="root note"))
    assert any("test/root" in r.permalink for r in results)


@pytest.mark.asyncio
async def test_pattern_matching(search_service, test_graph):
    """Test pattern matching with various wildcards."""
    # Test wildcards
    results = await search_service.search(SearchQuery(permalink_match="test/*"))
    for r in results:
        assert "test/" in r.permalink

    # Test start wildcards
    results = await search_service.search(SearchQuery(permalink_match="*/observations"))
    for r in results:
        assert "/observations" in r.permalink

    # Test permalink partial match
    results = await search_service.search(SearchQuery(permalink_match="test"))
    for r in results:
        assert "test/" in r.permalink


@pytest.mark.asyncio
async def test_filters(search_service, test_graph):
    """Test search filters."""
    # Combined filters
    results = await search_service.search(
        SearchQuery(text="Deep", entity_types=[SearchItemType.ENTITY], types=["deep"])
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
    results = await search_service.search(SearchQuery(types=["test"]))
    assert len(results) > 0
    for r in results:
        assert r.type == SearchItemType.ENTITY


@pytest.mark.asyncio
async def test_search_entity_type(search_service, test_graph):
    """Test search filters."""

    # Should find only type
    results = await search_service.search(SearchQuery(entity_types=[SearchItemType.ENTITY]))
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


@pytest.mark.asyncio
async def test_boolean_and_search(search_service, test_graph):
    """Test boolean AND search."""
    # Create an entity with specific terms for testing
    # This assumes the test_graph fixture already has entities with relevant terms

    # Test AND operator - both terms must be present
    results = await search_service.search(SearchQuery(text="Root AND Entity"))
    assert len(results) >= 1

    # Verify the result contains both terms
    found = False
    for result in results:
        if (result.title and "Root" in result.title and "Entity" in result.title) or (
            result.content_snippet
            and "Root" in result.content_snippet
            and "Entity" in result.content_snippet
        ):
            found = True
            break
    assert found, "Boolean AND search failed to find items containing both terms"

    # Verify that items with only one term are not returned
    results = await search_service.search(SearchQuery(text="NonexistentTerm AND Root"))
    assert len(results) == 0, "Boolean AND search returned results when it shouldn't have"


@pytest.mark.asyncio
async def test_boolean_or_search(search_service, test_graph):
    """Test boolean OR search."""
    # Test OR operator - either term can be present
    results = await search_service.search(SearchQuery(text="Root OR Connected"))

    # Should find both "Root Entity" and "Connected Entity"
    assert len(results) >= 2

    # Verify we find items with either term
    root_found = False
    connected_found = False

    for result in results:
        if result.permalink == "test/root":
            root_found = True
        elif "connected" in result.permalink.lower():
            connected_found = True

    assert root_found, "Boolean OR search failed to find 'Root' term"
    assert connected_found, "Boolean OR search failed to find 'Connected' term"


@pytest.mark.asyncio
async def test_boolean_not_search(search_service, test_graph):
    """Test boolean NOT search."""
    # Test NOT operator - exclude certain terms
    results = await search_service.search(SearchQuery(text="Entity NOT Connected"))

    # Should find "Root Entity" but not "Connected Entity"
    for result in results:
        assert "connected" not in result.permalink.lower(), (
            "Boolean NOT search returned excluded term"
        )


@pytest.mark.asyncio
async def test_boolean_group_search(search_service, test_graph):
    """Test boolean grouping with parentheses."""
    # Test grouping - (A OR B) AND C
    results = await search_service.search(SearchQuery(title="(Root OR Connected) AND Entity"))

    # Should find both entities that contain "Entity" and either "Root" or "Connected"
    assert len(results) >= 2

    for result in results:
        # Each result should contain "Entity" and either "Root" or "Connected"
        contains_entity = "entity" in result.title.lower()
        contains_root_or_connected = (
            "root" in result.title.lower() or "connected" in result.title.lower()
        )

        assert contains_entity and contains_root_or_connected, (
            "Boolean grouped search returned incorrect results"
        )


@pytest.mark.asyncio
async def test_boolean_operators_detection(search_service):
    """Test detection of boolean operators in query."""
    # Test various queries that should be detected as boolean
    boolean_queries = [
        "term1 AND term2",
        "term1 OR term2",
        "term1 NOT term2",
        "(term1 OR term2) AND term3",
        "complex (nested OR grouping) AND term",
    ]

    for query_text in boolean_queries:
        query = SearchQuery(text=query_text)
        assert query.has_boolean_operators(), f"Failed to detect boolean operators in: {query_text}"

    # Test queries that should not be detected as boolean
    non_boolean_queries = [
        "normal search query",
        "brand name",  # Should not detect "AND" within "brand"
        "understand this concept",  # Should not detect "AND" within "understand"
        "command line",
        "sandbox testing",
    ]

    for query_text in non_boolean_queries:
        query = SearchQuery(text=query_text)
        assert not query.has_boolean_operators(), (
            f"Incorrectly detected boolean operators in: {query_text}"
        )
