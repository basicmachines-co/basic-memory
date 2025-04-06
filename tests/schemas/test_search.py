"""Tests for search schemas."""

from datetime import datetime

from basic_memory.schemas.search import (
    SearchItemType,
    SearchQuery,
    SearchResult,
    SearchResponse,
)


def test_search_modes():
    """Test different search modes."""
    # Exact permalink
    query = SearchQuery(permalink="specs/search")
    assert query.permalink == "specs/search"
    assert query.text is None

    # Pattern match
    query = SearchQuery(permalink="specs/*")
    assert query.permalink == "specs/*"
    assert query.text is None

    # Text search
    query = SearchQuery(text="search implementation")
    assert query.text == "search implementation"
    assert query.permalink is None


def test_search_filters():
    """Test search result filtering."""
    query = SearchQuery(
        text="search",
        entity_types=[SearchItemType.ENTITY],
        types=["component"],
        after_date=datetime(2024, 1, 1),
    )
    assert query.entity_types == [SearchItemType.ENTITY]
    assert query.types == ["component"]
    assert query.after_date == "2024-01-01T00:00:00"


def test_search_result():
    """Test search result structure."""
    result = SearchResult(
        title="test",
        type=SearchItemType.ENTITY,
        entity="some_entity",
        score=0.8,
        metadata={"entity_type": "component"},
        permalink="specs/search",
        file_path="specs/search.md",
    )
    assert result.type == SearchItemType.ENTITY
    assert result.score == 0.8
    assert result.metadata == {"entity_type": "component"}


def test_observation_result():
    """Test observation result fields."""
    result = SearchResult(
        title="test",
        permalink="specs/search",
        file_path="specs/search.md",
        type=SearchItemType.OBSERVATION,
        score=0.5,
        metadata={},
        entity="some_entity",
        category="tech",
    )
    assert result.entity == "some_entity"
    assert result.category == "tech"


def test_relation_result():
    """Test relation result fields."""
    result = SearchResult(
        title="test",
        permalink="specs/search",
        file_path="specs/search.md",
        type=SearchItemType.RELATION,
        entity="some_entity",
        score=0.5,
        metadata={},
        from_entity="123",
        to_entity="456",
        relation_type="depends_on",
    )
    assert result.from_entity == "123"
    assert result.to_entity == "456"
    assert result.relation_type == "depends_on"


def test_search_response():
    """Test search response wrapper."""
    results = [
        SearchResult(
            title="test",
            permalink="specs/search",
            file_path="specs/search.md",
            type=SearchItemType.ENTITY,
            entity="some_entity",
            score=0.8,
            metadata={},
        ),
        SearchResult(
            title="test",
            permalink="specs/search",
            file_path="specs/search.md",
            type=SearchItemType.ENTITY,
            entity="some_entity",
            score=0.6,
            metadata={},
        ),
    ]
    response = SearchResponse(results=results, current_page=1, page_size=1)
    assert len(response.results) == 2
    assert response.results[0].score > response.results[1].score
