"""Latency benchmark for vector and hybrid search with the local reranker."""

from __future__ import annotations

import math
import time
from typing import cast

import pytest

from basic_memory.config import DatabaseBackend
from basic_memory.repository.rerank_provider import RerankProvider
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode
from basic_memory.services.search_service import SearchService

from semantic.conftest import SearchCombo, _create_fastembed_provider, create_search_service
from semantic.corpus import (
    GOLDEN_RANKING_DOCUMENTS,
    GOLDEN_RANKING_QUERIES,
    seed_ranking_documents,
)
from semantic.metrics import LatencyMetrics, format_latency_table


LATENCY_ITERATIONS = 20
CATASTROPHIC_P95_CEILING_MS = 10_000.0
LATENCY_COMBO = SearchCombo(
    "sqlite-fastembed-rerank",
    DatabaseBackend.SQLITE,
    "fastembed",
    384,
)


async def _search_once(
    search_service: SearchService,
    query_text: str,
    mode: SearchRetrievalMode,
) -> None:
    results = await search_service.search(
        SearchQuery(
            text=query_text,
            retrieval_mode=mode,
            entity_types=[SearchItemType.ENTITY],
            min_similarity=0.0,
        ),
        limit=10,
    )
    assert results


async def _measure_latency(
    search_service: SearchService,
    repository: SQLiteSearchRepository,
    rerank_provider: RerankProvider | None,
    mode: SearchRetrievalMode,
) -> LatencyMetrics:
    """Warm one configuration, then collect the fixed twenty-sample window."""
    repository._rerank_provider = rerank_provider
    configuration = "reranker-on" if rerank_provider is not None else "reranker-off"
    metrics = LatencyMetrics(configuration=configuration, mode=mode.value)

    # The warmup owns lazy model construction and ONNX graph initialization, so
    # the reported samples represent steady-state local search latency.
    await _search_once(search_service, GOLDEN_RANKING_QUERIES[0].text, mode)
    for index in range(LATENCY_ITERATIONS):
        case = GOLDEN_RANKING_QUERIES[index % len(GOLDEN_RANKING_QUERIES)]
        started = time.perf_counter()
        await _search_once(search_service, case.text, mode)
        metrics.latencies.append(time.perf_counter() - started)

    return metrics


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_local_reranker_vector_and_hybrid_latency(
    sqlite_engine_factory,
    tmp_path,
) -> None:
    """Report steady-state local rerank overhead without a timing-sensitive quality gate."""
    search_service = await create_search_service(
        sqlite_engine_factory,
        LATENCY_COMBO,
        tmp_path,
        embedding_provider=_create_fastembed_provider(),
        reranker_enabled=True,
    )
    await seed_ranking_documents(search_service, GOLDEN_RANKING_DOCUMENTS)

    repository = cast(SQLiteSearchRepository, search_service.repository)
    rerank_provider = repository._rerank_provider
    assert rerank_provider is not None

    measurements: list[LatencyMetrics] = []
    for mode in (SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID):
        measurements.append(await _measure_latency(search_service, repository, None, mode))
        measurements.append(
            await _measure_latency(search_service, repository, rerank_provider, mode)
        )

    print(f"\n{format_latency_table(measurements)}")

    for metrics in measurements:
        assert len(metrics.latencies) == LATENCY_ITERATIONS
        assert math.isfinite(metrics.p50_ms)
        assert math.isfinite(metrics.p95_ms)
        if metrics.configuration == "reranker-on":
            assert metrics.p95_ms < CATASTROPHIC_P95_CEILING_MS

    for baseline, reranked in zip(measurements[::2], measurements[1::2], strict=True):
        assert baseline.mode == reranked.mode
        assert math.isfinite(reranked.p95_ms - baseline.p95_ms)
