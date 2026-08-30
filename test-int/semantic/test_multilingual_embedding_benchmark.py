"""On-demand multilingual FastEmbed quality and operational benchmark."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from basic_memory.config import DatabaseBackend
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.schemas.search import SearchItemType, SearchQuery

from semantic.conftest import SearchCombo, create_search_service, skip_if_needed
from semantic.multilingual_benchmark import (
    RuntimeMeasurements,
    RetrievalObservation,
    benchmark_context,
    benchmark_retrieval_mode,
    benchmark_storage_case,
    embedding_benchmark_config,
    embedding_model_case,
    model_cache_size_bytes,
    process_peak_rss_bytes,
    process_rss_bytes,
    result_permalinks,
    retrieval_summaries,
    vector_storage_size_bytes,
    write_multilingual_benchmark_artifact,
)
from semantic.multilingual_corpus import MULTILINGUAL_CORPUS, seed_multilingual_documents


@pytest.mark.asyncio
@pytest.mark.benchmark
async def test_multilingual_embedding_benchmark(
    sqlite_engine_factory,
    postgres_engine_factory,
    tmp_path: Path,
) -> None:
    """Measure one model/backend pair in an isolated pytest process."""
    model_case = embedding_model_case(os.getenv("BASIC_MEMORY_MULTILINGUAL_MODEL", "bge-small-en"))
    storage_case = benchmark_storage_case(os.getenv("BASIC_MEMORY_MULTILINGUAL_BACKEND", "sqlite"))
    backend = storage_case.database_backend
    retrieval_mode = benchmark_retrieval_mode(
        os.getenv("BASIC_MEMORY_MULTILINGUAL_RETRIEVAL_MODE", "vector")
    )
    production_threshold = float(os.getenv("BASIC_MEMORY_MULTILINGUAL_THRESHOLD", "0.55"))
    if not 0.0 <= production_threshold <= 1.0:
        raise ValueError("BASIC_MEMORY_MULTILINGUAL_THRESHOLD must be between 0.0 and 1.0")

    combo = SearchCombo(
        name=f"{storage_case.key}-{model_case.key}",
        backend=backend,
        provider_name="fastembed",
        dimensions=model_case.dimensions,
    )
    skip_if_needed(combo)

    if backend is DatabaseBackend.SQLITE:
        engine_factory_result = sqlite_engine_factory
    else:
        if postgres_engine_factory is None:
            pytest.skip("Postgres engine not available")
        engine_factory_result = postgres_engine_factory
    engine, _ = engine_factory_result

    milvus_storage_directory = tmp_path / "milvus"
    milvus_uri = None
    if storage_case.vector_index_name == "milvus":
        milvus_storage_directory.mkdir()
        milvus_uri = str(milvus_storage_directory / "vectors.db")

    benchmark_config = embedding_benchmark_config(
        model_case,
        storage_case,
        milvus_uri=milvus_uri,
    )
    provider = create_embedding_provider(benchmark_config)

    # Each invocation benchmarks exactly one model, so this first embedding is a genuine
    # process-cold model load while later indexing and queries exercise the warmed provider.
    rss_before_load = process_rss_bytes()
    load_started = time.perf_counter()
    warmup_vector = await provider.embed_query("multilingual benchmark warmup")
    cold_load_seconds = time.perf_counter() - load_started
    rss_after_load = process_rss_bytes()
    assert len(warmup_vector) == model_case.dimensions

    search_service = await create_search_service(
        engine_factory_result,
        combo,
        tmp_path,
        embedding_provider=provider,
        benchmark_config=benchmark_config,
    )

    indexing_started = time.perf_counter()
    entities = await seed_multilingual_documents(
        search_service,
        MULTILINGUAL_CORPUS.documents,
    )
    indexing_seconds = time.perf_counter() - indexing_started
    assert len(entities) == len(MULTILINGUAL_CORPUS.documents)
    rss_after_index = process_rss_bytes()

    observations: list[RetrievalObservation] = []
    for query in MULTILINGUAL_CORPUS.queries:
        ranking_started = time.perf_counter()
        ranked_results = await search_service.search(
            SearchQuery(
                text=query.text,
                retrieval_mode=retrieval_mode,
                entity_types=[SearchItemType.ENTITY],
                min_similarity=0.0,
            ),
            limit=10,
        )
        ranking_latency = time.perf_counter() - ranking_started

        accepted_started = time.perf_counter()
        accepted_results = await search_service.search(
            SearchQuery(
                text=query.text,
                retrieval_mode=retrieval_mode,
                entity_types=[SearchItemType.ENTITY],
                min_similarity=production_threshold,
            ),
            limit=10,
        )
        accepted_latency = time.perf_counter() - accepted_started

        observations.append(
            RetrievalObservation(
                query=query,
                ranked_permalinks=result_permalinks(ranked_results),
                accepted_permalinks=result_permalinks(accepted_results),
                ranking_latency_seconds=ranking_latency,
                accepted_latency_seconds=accepted_latency,
            )
        )

    summaries = retrieval_summaries(observations)
    runtime = RuntimeMeasurements(
        document_count=len(entities),
        cold_load_seconds=cold_load_seconds,
        indexing_seconds=indexing_seconds,
        rss_before_load_bytes=rss_before_load,
        rss_after_load_bytes=rss_after_load,
        rss_after_index_bytes=rss_after_index,
        peak_rss_bytes=process_peak_rss_bytes(),
        model_cache_bytes=model_cache_size_bytes(model_case),
        vector_storage_bytes=await vector_storage_size_bytes(
            engine,
            storage_case,
            milvus_storage_directory=milvus_storage_directory,
        ),
    )
    context = benchmark_context(
        model_case,
        storage_case,
        retrieval_mode,
        MULTILINGUAL_CORPUS.version,
        production_threshold,
    )

    print("\nMultilingual benchmark context")
    print(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True))
    print("\nMultilingual benchmark quality")
    print(
        json.dumps(
            {summary.name: summary.as_metrics() for summary in summaries},
            indent=2,
            sort_keys=True,
        )
    )
    print("\nMultilingual benchmark runtime")
    print(json.dumps(runtime.as_metrics(), indent=2, sort_keys=True))

    output_value = os.getenv("BASIC_MEMORY_BENCHMARK_OUTPUT")
    if output_value:
        write_multilingual_benchmark_artifact(
            Path(output_value).expanduser(),
            context,
            summaries,
            runtime,
            observations,
        )
