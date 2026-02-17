"""Focused multi-term semantic retrieval integration tests.

These tests validate that multi-word and natural-language queries retrieve
relevant results over the semantic benchmark fixture corpus.
"""

from __future__ import annotations

import pytest

from basic_memory.config import DatabaseBackend
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode

from semantic.conftest import (
    ALL_COMBOS,
    SearchCombo,
    create_search_service,
    skip_if_needed,
    _create_fastembed_provider,
)
from semantic.corpus import QueryCase, seed_benchmark_notes
from semantic.metrics import first_relevant_rank


FASTEMBED_COMBOS = [combo for combo in ALL_COMBOS if combo.provider_name == "fastembed"]

# High-signal natural-language multi-term queries mapped to benchmark topics.
NATURAL_LANGUAGE_MULTI_TERM_CASES = [
    QueryCase(
        text="How do access tokens and refresh tokens work for login sessions?",
        expected_topic="auth",
    ),
    QueryCase(
        text="How do schema migrations and database indexes improve query performance?",
        expected_topic="database",
    ),
    QueryCase(
        text="How does the filesystem watcher detect changes and trigger reindex?",
        expected_topic="sync",
    ),
    QueryCase(
        text="How does the agent build memory context for retrieval across sessions?",
        expected_topic="agent",
    ),
]

# Keep vector assertions on the most stable topic queries.
LEXICAL_MULTI_TERM_CASES = [
    QueryCase(text="authentication session token oauth refresh login", expected_topic="auth"),
    QueryCase(text="database migration schema sqlite postgres index", expected_topic="database"),
]

HYBRID_MIN_HITS_AT_5 = {
    "sqlite-fastembed": 2,
    "postgres-fastembed": 3,
}

VECTOR_MIN_HITS_AT_10 = {
    "sqlite-fastembed": 1,
    "postgres-fastembed": 2,
}


async def _create_service_for_combo(
    combo: SearchCombo,
    sqlite_engine_factory,
    postgres_engine_factory,
    tmp_path,
):
    """Create a configured SearchService for the selected backend/provider combo."""
    if combo.backend == DatabaseBackend.SQLITE:
        engine_factory_result = sqlite_engine_factory
    else:
        if postgres_engine_factory is None:
            pytest.skip("Postgres engine not available")
        engine_factory_result = postgres_engine_factory

    provider = _create_fastembed_provider()
    return await create_search_service(
        engine_factory_result, combo, tmp_path, embedding_provider=provider
    )


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.parametrize("combo", FASTEMBED_COMBOS, ids=[c.name for c in FASTEMBED_COMBOS])
async def test_multiterm_paraphrase_queries_rank_expected_topic_with_hybrid(
    combo: SearchCombo,
    sqlite_engine_factory,
    postgres_engine_factory,
    tmp_path,
):
    """Hybrid retrieval should rank relevant topic notes for natural-language multi-term queries."""
    skip_if_needed(combo)

    search_service = await _create_service_for_combo(
        combo,
        sqlite_engine_factory,
        postgres_engine_factory,
        tmp_path,
    )
    await seed_benchmark_notes(search_service, note_count=120)

    hits_at_5 = 0
    for case in NATURAL_LANGUAGE_MULTI_TERM_CASES:
        results = await search_service.search(
            SearchQuery(
                text=case.text,
                retrieval_mode=SearchRetrievalMode.HYBRID,
                entity_types=[SearchItemType.ENTITY],
            ),
            limit=10,
        )

        assert results, f"No results for query: {case.text}"
        rank = first_relevant_rank(results, case.expected_topic, k=5)
        if rank is not None:
            hits_at_5 += 1

    min_hits = HYBRID_MIN_HITS_AT_5[combo.name]
    assert hits_at_5 >= min_hits, (
        f"Hybrid multi-term relevance too low for {combo.name}: "
        f"hits@5={hits_at_5}/{len(NATURAL_LANGUAGE_MULTI_TERM_CASES)} (min={min_hits})"
    )


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.parametrize("combo", FASTEMBED_COMBOS, ids=[c.name for c in FASTEMBED_COMBOS])
async def test_multiterm_lexical_queries_return_relevant_topic_with_vector(
    combo: SearchCombo,
    sqlite_engine_factory,
    postgres_engine_factory,
    tmp_path,
):
    """Vector-only retrieval should return relevant notes for multi-term lexical queries."""
    skip_if_needed(combo)

    search_service = await _create_service_for_combo(
        combo,
        sqlite_engine_factory,
        postgres_engine_factory,
        tmp_path,
    )
    await seed_benchmark_notes(search_service, note_count=120)

    hits_at_10 = 0
    for case in LEXICAL_MULTI_TERM_CASES:
        results = await search_service.search(
            SearchQuery(
                text=case.text,
                retrieval_mode=SearchRetrievalMode.VECTOR,
                entity_types=[SearchItemType.ENTITY],
            ),
            limit=10,
        )

        assert results, f"No results for query: {case.text}"
        rank = first_relevant_rank(results, case.expected_topic, k=10)
        if rank is not None:
            hits_at_10 += 1

    min_hits = VECTOR_MIN_HITS_AT_10[combo.name]
    assert hits_at_10 >= min_hits, (
        f"Vector multi-term relevance too low for {combo.name}: "
        f"hits@10={hits_at_10}/{len(LEXICAL_MULTI_TERM_CASES)} (min={min_hits})"
    )
