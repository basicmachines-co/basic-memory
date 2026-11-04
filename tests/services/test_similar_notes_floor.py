"""The server applies the similar-note advisory's floor for its own model (#1982)."""

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.similar_notes_floor import (
    SIMILAR_NOTES_MIN_SIMILARITY,
    similar_notes_min_similarity,
)
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode, SimilarityFloor
from basic_memory.services.search_service import SearchService


def _vector_query(**fields) -> SearchQuery:
    return SearchQuery(text="coffee brewing", retrieval_mode=SearchRetrievalMode.VECTOR, **fields)


def test_measured_model_gets_the_advisory_floor() -> None:
    assert similar_notes_min_similarity(BasicMemoryConfig.model_construct()) == pytest.approx(
        SIMILAR_NOTES_MIN_SIMILARITY
    )


def test_other_models_keep_their_search_floor() -> None:
    openai = BasicMemoryConfig.model_construct(semantic_embedding_provider="openai")
    other_fastembed = BasicMemoryConfig.model_construct(
        semantic_embedding_model="BAAI/bge-base-en-v1.5", semantic_embedding_dimensions=768
    )

    assert similar_notes_min_similarity(openai) is None
    assert similar_notes_min_similarity(other_fastembed) is None


def test_advisory_query_runs_with_the_repository_floor(search_service: SearchService) -> None:
    prepared = search_service.prepare_query(
        _vector_query(similarity_floor=SimilarityFloor.SIMILAR_NOTES)
    )

    assert prepared is not None
    assert prepared.min_similarity == pytest.approx(
        search_service.repository.configured_similar_notes_min_similarity
    )


def test_explicit_min_similarity_wins_over_the_advisory_floor(
    search_service: SearchService,
) -> None:
    prepared = search_service.prepare_query(
        _vector_query(similarity_floor=SimilarityFloor.SIMILAR_NOTES, min_similarity=0.31)
    )

    assert prepared is not None
    assert prepared.min_similarity == pytest.approx(0.31)


def test_ordinary_search_keeps_the_configured_search_floor(search_service: SearchService) -> None:
    prepared = search_service.prepare_query(_vector_query())

    assert prepared is not None
    # None means the reader applies semantic_min_similarity, as before.
    assert prepared.min_similarity is None


def test_repository_floor_falls_back_to_the_search_floor(
    search_service: SearchService, app_config: BasicMemoryConfig
) -> None:
    repository = search_service.repository
    expected = similar_notes_min_similarity(app_config)

    assert repository.configured_similar_notes_min_similarity == pytest.approx(
        repository.configured_min_similarity if expected is None else expected
    )
