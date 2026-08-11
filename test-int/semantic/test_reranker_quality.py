"""Golden-query quality regression coverage for the local reranker."""

from __future__ import annotations

import time
from typing import cast

import pytest

from basic_memory.config import DatabaseBackend
from basic_memory.repository.rerank_provider import RerankProvider
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode

from semantic.conftest import SearchCombo, _create_fastembed_provider, create_search_service
from semantic.corpus import (
    GOLDEN_RANKING_DOCUMENTS,
    GOLDEN_RANKING_QUERIES,
    RankingQuery,
    seed_ranking_documents,
)
from semantic.metrics import (
    QualityMetrics,
    first_permalink_rank,
    format_reranker_quality_table,
    write_benchmark_artifact,
)


QUALITY_COMBO = SearchCombo(
    "sqlite-fastembed-rerank",
    DatabaseBackend.SQLITE,
    "fastembed",
    384,
)


async def _evaluate_quality(
    repository: SQLiteSearchRepository,
    rerank_provider: RerankProvider | None,
    cases: tuple[RankingQuery, ...],
) -> QualityMetrics:
    """Run one fixed retrieval configuration over every golden query."""
    repository._rerank_provider = rerank_provider
    configuration = "reranker-on" if rerank_provider is not None else "reranker-off"
    metrics = QualityMetrics(combo=configuration, suite="golden", mode="hybrid")

    for case in cases:
        started = time.perf_counter()
        results = await repository.search(
            search_text=case.text,
            retrieval_mode=SearchRetrievalMode.HYBRID,
            search_item_types=[SearchItemType.ENTITY],
            min_similarity=0.0,
            limit=10,
        )
        latency = time.perf_counter() - started
        rank = first_permalink_rank(results, case.expected_permalink, k=10)
        metrics.record(case.text, case.expected_permalink, rank, latency=latency)

    return metrics


@pytest.mark.asyncio
@pytest.mark.semantic
async def test_real_reranker_preserves_or_improves_golden_query_quality(
    sqlite_engine_factory,
    tmp_path,
) -> None:
    """Cross-encoder reranking must improve a case without regressing aggregate quality."""
    search_service = await create_search_service(
        sqlite_engine_factory,
        QUALITY_COMBO,
        tmp_path,
        embedding_provider=_create_fastembed_provider(),
        reranker_enabled=True,
    )
    await seed_ranking_documents(search_service, GOLDEN_RANKING_DOCUMENTS)

    repository = cast(SQLiteSearchRepository, search_service.repository)
    rerank_provider = repository._rerank_provider
    assert rerank_provider is not None

    baseline = await _evaluate_quality(repository, None, GOLDEN_RANKING_QUERIES)
    reranked = await _evaluate_quality(repository, rerank_provider, GOLDEN_RANKING_QUERIES)
    print(f"\n{format_reranker_quality_table([baseline, reranked])}")
    write_benchmark_artifact([baseline, reranked])

    assert reranked.hit_at_1 >= baseline.hit_at_1
    assert reranked.mrr_at_10 >= baseline.mrr_at_10
    assert reranked.hit_at_5 == 1.0
    assert any(
        reranked_case["rank"] is not None
        and baseline_case["rank"] is not None
        and reranked_case["rank"] < baseline_case["rank"]
        for baseline_case, reranked_case in zip(
            baseline.per_query,
            reranked.per_query,
            strict=True,
        )
    )
