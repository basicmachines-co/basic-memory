"""Tests for search schemas."""

from datetime import datetime

from basic_memory.schemas.search import (
    SearchItemType,
    SearchQuery,
    SearchRetrievalMode,
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
        note_types=["component"],
        after_date=datetime(2024, 1, 1),
    )
    assert query.entity_types == [SearchItemType.ENTITY]
    assert query.note_types == ["component"]
    assert query.after_date == "2024-01-01T00:00:00"


def test_search_retrieval_mode_defaults_to_fts():
    """Search retrieval mode defaults to FTS and accepts vector modes."""
    query = SearchQuery(text="search implementation")
    assert query.retrieval_mode == SearchRetrievalMode.FTS

    vector_query = SearchQuery(text="search implementation", retrieval_mode="vector")
    assert vector_query.retrieval_mode == SearchRetrievalMode.VECTOR


def test_search_result():
    """Test search result structure."""
    result = SearchResult(
        title="test",
        type=SearchItemType.ENTITY,
        entity="some_entity",
        score=0.8,
        metadata={"note_type": "component"},
        permalink="specs/search",
        file_path="specs/search.md",
    )
    assert result.type == SearchItemType.ENTITY
    assert result.score == 0.8
    assert result.metadata == {"note_type": "component"}


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


def test_metadata_filters_note_type_routed_to_note_types():
    """metadata_filters with note_type should be intercepted and routed to note_types.

    note_type lives in search_index.metadata, NOT entity.entity_metadata.
    Passing it through metadata_filters would query the wrong column and return
    empty results. The validator intercepts it and moves it to note_types.
    """
    query = SearchQuery(metadata_filters={"note_type": "note"})
    assert query.note_types == ["note"]
    assert query.metadata_filters is None


def test_metadata_filters_note_type_list_routed_to_note_types():
    """metadata_filters with note_type as list is normalized and merged into note_types."""
    query = SearchQuery(metadata_filters={"note_type": ["note", "spec"]})
    assert query.note_types == ["note", "spec"]
    assert query.metadata_filters is None


def test_metadata_filters_note_type_merges_with_existing_note_types():
    """note_type from metadata_filters is merged with existing note_types, deduped."""
    query = SearchQuery(note_types=["spec"], metadata_filters={"note_type": "note"})
    assert "spec" in query.note_types
    assert "note" in query.note_types
    assert query.metadata_filters is None


def test_metadata_filters_note_type_deduplicates():
    """note_type already in note_types is not added twice."""
    query = SearchQuery(note_types=["note"], metadata_filters={"note_type": "note"})
    assert query.note_types.count("note") == 1
    assert query.metadata_filters is None


def test_metadata_filters_non_system_fields_untouched():
    """metadata_filters with non-system fields are passed through unchanged."""
    query = SearchQuery(metadata_filters={"status": "active", "priority": "high"})
    assert query.metadata_filters == {"status": "active", "priority": "high"}
    assert query.note_types is None


def test_metadata_filters_mixed_system_and_user_fields():
    """note_type is extracted; remaining metadata_filters are preserved."""
    query = SearchQuery(metadata_filters={"note_type": "spec", "status": "active"})
    assert query.note_types == ["spec"]
    assert query.metadata_filters == {"status": "active"}


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
