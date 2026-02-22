"""Performance benchmarks for local semantic search indexing and query modes."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import DatabaseBackend
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode


TOPIC_TERMS = {
    "auth": ["authentication", "session", "token", "oauth", "refresh", "login"],
    "database": ["database", "migration", "schema", "sqlite", "postgres", "index"],
    "sync": ["sync", "filesystem", "watcher", "checksum", "reindex", "changes"],
    "agent": ["agent", "memory", "context", "prompt", "retrieval", "tooling"],
}


@dataclass(frozen=True)
class QueryCase:
    text: str
    expected_topic: str


@dataclass(frozen=True)
class QualityQueryCase:
    text: str
    expected_topic: str


QUALITY_QUERY_SUITES: dict[str, list[QualityQueryCase]] = {
    "lexical": [
        QualityQueryCase(text="token refresh login", expected_topic="auth"),
        QualityQueryCase(text="schema migration postgres index", expected_topic="database"),
        QualityQueryCase(text="filesystem watcher checksum reindex", expected_topic="sync"),
        QualityQueryCase(text="agent memory context retrieval", expected_topic="agent"),
    ],
    "paraphrase": [
        QualityQueryCase(
            text="How do we keep sign-in state and rotate refresh credentials?",
            expected_topic="auth",
        ),
        QualityQueryCase(
            text="What is our approach for evolving DB structure and migration strategy?",
            expected_topic="database",
        ),
        QualityQueryCase(
            text="How do we detect note edits and trigger a reindex pass?",
            expected_topic="sync",
        ),
        QualityQueryCase(
            text="How does the assistant preserve long-term context for tool use?",
            expected_topic="agent",
        ),
    ],
}


def _skip_if_not_sqlite(app_config) -> None:
    if app_config.database_backend != DatabaseBackend.SQLITE:
        pytest.skip("These benchmarks target local SQLite semantic search.")


def _enable_semantic_for_benchmark(search_service, app_config) -> None:
    repository = search_service.repository
    if not isinstance(repository, SQLiteSearchRepository):
        return

    app_config.semantic_search_enabled = True
    repository._semantic_enabled = True
    if repository._embedding_provider is None:
        repository._embedding_provider = FastEmbedEmbeddingProvider(
            model_name=app_config.semantic_embedding_model,
            batch_size=app_config.semantic_embedding_batch_size,
        )
    repository._vector_dimensions = repository._embedding_provider.dimensions
    repository._vector_tables_initialized = False


def _build_benchmark_content(topic: str, terms: list[str], note_index: int) -> str:
    repeated_phrase = " ".join(terms)
    return f"""---
tags: [benchmark, {topic}]
status: active
---
# {topic.title()} Benchmark Note {note_index}

## Summary
This note covers {topic} workflows and practical implementation choices.
Primary concepts: {repeated_phrase}.

## Decisions
Decision details for {topic} note {note_index}.
{repeated_phrase}
{repeated_phrase}

## Deep Detail
Detailed examples for {topic} note {note_index} with operational context.
{repeated_phrase}
{repeated_phrase}
{repeated_phrase}
"""


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = math.ceil((percentile / 100.0) * len(sorted_values)) - 1
    index = max(0, min(rank, len(sorted_values) - 1))
    return sorted_values[index]


def _parse_threshold(env_var: str) -> float | None:
    raw_value = os.getenv(env_var)
    if raw_value is None or not raw_value.strip():
        return None
    try:
        return float(raw_value)
    except ValueError as exc:  # pragma: no cover - config error path
        raise ValueError(f"{env_var} must be a float, got {raw_value!r}") from exc


def _enforce_min_threshold(metric_name: str, actual: float, env_var: str) -> None:
    threshold = _parse_threshold(env_var)
    if threshold is None:
        return
    assert actual >= threshold, (
        f"Benchmark guardrail failed for {metric_name}: {actual:.4f} < {threshold:.4f} ({env_var})"
    )


def _enforce_max_threshold(metric_name: str, actual: float, env_var: str) -> None:
    threshold = _parse_threshold(env_var)
    if threshold is None:
        return
    assert actual <= threshold, (
        f"Benchmark guardrail failed for {metric_name}: {actual:.4f} > {threshold:.4f} ({env_var})"
    )


def _write_benchmark_artifact(name: str, metrics: dict[str, float | int | str]) -> None:
    output_path = os.getenv("BASIC_MEMORY_BENCHMARK_OUTPUT")
    if not output_path:
        return

    artifact_path = Path(output_path).expanduser()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "benchmark": name,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }
    with artifact_path.open("a", encoding="utf-8") as artifact_file:
        artifact_file.write(json.dumps(payload, sort_keys=True) + "\n")


def _print_index_metrics(
    name: str, note_count: int, elapsed_seconds: float, db_size_bytes: int
) -> dict[str, float | int | str]:
    notes_per_second = note_count / elapsed_seconds if elapsed_seconds else 0.0
    sqlite_size_mb = db_size_bytes / (1024 * 1024)
    metrics: dict[str, float | int | str] = {
        "notes_indexed": note_count,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "notes_per_sec": round(notes_per_second, 6),
        "sqlite_size_bytes": db_size_bytes,
        "sqlite_size_mb": round(sqlite_size_mb, 6),
    }
    print(f"\nBENCHMARK: {name}")
    print(f"notes indexed: {note_count}")
    print(f"elapsed (s): {elapsed_seconds:.4f}")
    print(f"notes/sec: {notes_per_second:.2f}")
    print(f"sqlite size (MB): {sqlite_size_mb:.2f}")
    return metrics


def _print_query_metrics(name: str, latencies: list[float]) -> dict[str, float | int | str]:
    latencies_ms = [latency * 1000 for latency in latencies]
    avg_ms = mean(latencies_ms)
    p50_ms = _percentile(latencies_ms, 50)
    p95_ms = _percentile(latencies_ms, 95)
    p99_ms = _percentile(latencies_ms, 99)
    metrics: dict[str, float | int | str] = {
        "queries_executed": len(latencies_ms),
        "avg_ms": round(avg_ms, 6),
        "p50_ms": round(p50_ms, 6),
        "p95_ms": round(p95_ms, 6),
        "p99_ms": round(p99_ms, 6),
    }
    print(f"\nBENCHMARK: {name}")
    print(f"queries executed: {len(latencies_ms)}")
    print(f"avg latency (ms): {avg_ms:.2f}")
    print(f"p50 latency (ms): {p50_ms:.2f}")
    print(f"p95 latency (ms): {p95_ms:.2f}")
    print(f"p99 latency (ms): {p99_ms:.2f}")
    return metrics


def _print_quality_metrics(
    name: str,
    *,
    cases: int,
    hit_rate_at_1: float,
    recall_at_5: float,
    mrr_at_10: float,
) -> dict[str, float | int | str]:
    metrics: dict[str, float | int | str] = {
        "cases": cases,
        "hit_rate_at_1": round(hit_rate_at_1, 6),
        "recall_at_5": round(recall_at_5, 6),
        "mrr_at_10": round(mrr_at_10, 6),
    }
    print(f"\nBENCHMARK: {name}")
    print(f"cases: {cases}")
    print(f"hit@1: {hit_rate_at_1:.3f}")
    print(f"recall@5: {recall_at_5:.3f}")
    print(f"mrr@10: {mrr_at_10:.3f}")
    return metrics


def _first_relevant_rank(results, expected_topic: str, k: int) -> int | None:
    expected_prefix = f"bench/{expected_topic}-"
    for rank, row in enumerate(results[:k], start=1):
        if (row.permalink or "").startswith(expected_prefix):
            return rank
    return None


async def _seed_benchmark_notes(search_service, note_count: int):
    entities = []
    topic_names = list(TOPIC_TERMS.keys())

    for note_index in range(note_count):
        topic = topic_names[note_index % len(topic_names)]
        terms = TOPIC_TERMS[topic]
        permalink = f"bench/{topic}-{note_index:05d}"
        entity = await search_service.entity_repository.create(
            {
                "title": f"{topic.title()} Benchmark Note {note_index}",
                "note_type": "benchmark",
                "entity_metadata": {"tags": ["benchmark", topic], "status": "active"},
                "content_type": "text/markdown",
                "permalink": permalink,
                "file_path": f"{permalink}.md",
            }
        )
        content = _build_benchmark_content(topic, terms, note_index)
        await search_service.index_entity_data(entity, content=content)
        if isinstance(search_service.repository, SQLiteSearchRepository):
            if search_service.repository._semantic_enabled:
                await search_service.sync_entity_vectors(entity.id)
        entities.append(entity)

    return entities


async def _sqlite_size_bytes(search_service) -> int:
    async with db.scoped_session(search_service.repository.session_maker) as session:
        page_count_result = await session.execute(text("PRAGMA page_count"))
        page_size_result = await session.execute(text("PRAGMA page_size"))
        page_count = int(page_count_result.scalar_one())
        page_size = int(page_size_result.scalar_one())
        return page_count * page_size


@pytest.mark.asyncio
@pytest.mark.benchmark
async def test_benchmark_search_index_cold_start_300_notes(search_service, app_config):
    """Benchmark end-to-end indexing throughput for a cold local search index."""
    _skip_if_not_sqlite(app_config)
    _enable_semantic_for_benchmark(search_service, app_config)

    note_count = 300
    start = time.perf_counter()
    entities = await _seed_benchmark_notes(search_service, note_count=note_count)
    elapsed_seconds = time.perf_counter() - start
    db_size_bytes = await _sqlite_size_bytes(search_service)

    assert len(entities) == note_count
    assert elapsed_seconds > 0

    benchmark_name = "cold index (300 notes)"
    metrics = _print_index_metrics(
        name=benchmark_name,
        note_count=note_count,
        elapsed_seconds=elapsed_seconds,
        db_size_bytes=db_size_bytes,
    )
    _write_benchmark_artifact(benchmark_name, metrics)
    _enforce_min_threshold(
        metric_name="cold.notes_per_sec",
        actual=float(metrics["notes_per_sec"]),
        env_var="BASIC_MEMORY_BENCH_MIN_COLD_NOTES_PER_SEC",
    )
    _enforce_max_threshold(
        metric_name="cold.sqlite_size_mb",
        actual=float(metrics["sqlite_size_mb"]),
        env_var="BASIC_MEMORY_BENCH_MAX_COLD_SQLITE_SIZE_MB",
    )


@pytest.mark.asyncio
@pytest.mark.benchmark
async def test_benchmark_search_query_latency_by_mode(search_service, app_config):
    """Benchmark search latency for fts/vector/hybrid retrieval modes."""
    _skip_if_not_sqlite(app_config)
    _enable_semantic_for_benchmark(search_service, app_config)

    await _seed_benchmark_notes(search_service, note_count=240)

    query_cases = [
        QueryCase(text="session token login", expected_topic="auth"),
        QueryCase(text="schema migration sqlite", expected_topic="database"),
        QueryCase(text="filesystem watcher checksum", expected_topic="sync"),
        QueryCase(text="agent memory retrieval", expected_topic="agent"),
    ]
    passes_per_mode = 8

    for mode in (
        SearchRetrievalMode.FTS,
        SearchRetrievalMode.VECTOR,
        SearchRetrievalMode.HYBRID,
    ):
        latencies: list[float] = []
        for _ in range(passes_per_mode):
            for case in query_cases:
                start = time.perf_counter()
                results = await search_service.search(
                    SearchQuery(
                        text=case.text,
                        retrieval_mode=mode,
                        entity_types=[SearchItemType.ENTITY],
                    ),
                    limit=10,
                )
                latencies.append(time.perf_counter() - start)

                assert results
                assert any(
                    (row.permalink or "").startswith(f"bench/{case.expected_topic}-")
                    for row in results
                )

        benchmark_name = f"query latency ({mode.value})"
        metrics = _print_query_metrics(name=benchmark_name, latencies=latencies)
        _write_benchmark_artifact(benchmark_name, metrics)
        _enforce_max_threshold(
            metric_name=f"{mode.value}.p95_ms",
            actual=float(metrics["p95_ms"]),
            env_var=f"BASIC_MEMORY_BENCH_MAX_{mode.value.upper()}_P95_MS",
        )
        _enforce_max_threshold(
            metric_name=f"{mode.value}.p99_ms",
            actual=float(metrics["p99_ms"]),
            env_var=f"BASIC_MEMORY_BENCH_MAX_{mode.value.upper()}_P99_MS",
        )


@pytest.mark.asyncio
@pytest.mark.benchmark
@pytest.mark.slow
async def test_benchmark_search_incremental_reindex_80_of_800_notes(search_service, app_config):
    """Benchmark incremental re-index throughput for changed notes only."""
    _skip_if_not_sqlite(app_config)
    _enable_semantic_for_benchmark(search_service, app_config)

    entities = await _seed_benchmark_notes(search_service, note_count=800)
    changed_count = 80

    start = time.perf_counter()
    for note_index, entity in enumerate(entities[:changed_count]):
        topic = "auth" if note_index % 2 == 0 else "sync"
        terms = TOPIC_TERMS[topic]
        updated_content = (
            _build_benchmark_content(topic, terms, note_index)
            + f"\n\n## Incremental Marker\nincremental-marker-{note_index}\n"
        )
        await search_service.index_entity_data(entity, content=updated_content)
        await search_service.sync_entity_vectors(entity.id)
    elapsed_seconds = time.perf_counter() - start
    db_size_bytes = await _sqlite_size_bytes(search_service)

    verification = await search_service.search(
        SearchQuery(
            text="incremental-marker-5",
            retrieval_mode=SearchRetrievalMode.HYBRID,
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=20,
    )

    assert verification
    assert elapsed_seconds > 0

    benchmark_name = "incremental reindex (80 changed of 800)"
    metrics = _print_index_metrics(
        name=benchmark_name,
        note_count=changed_count,
        elapsed_seconds=elapsed_seconds,
        db_size_bytes=db_size_bytes,
    )
    _write_benchmark_artifact(benchmark_name, metrics)
    _enforce_min_threshold(
        metric_name="incremental.notes_per_sec",
        actual=float(metrics["notes_per_sec"]),
        env_var="BASIC_MEMORY_BENCH_MIN_INCREMENTAL_NOTES_PER_SEC",
    )
    _enforce_max_threshold(
        metric_name="incremental.sqlite_size_mb",
        actual=float(metrics["sqlite_size_mb"]),
        env_var="BASIC_MEMORY_BENCH_MAX_INCREMENTAL_SQLITE_SIZE_MB",
    )


@pytest.mark.asyncio
@pytest.mark.benchmark
async def test_benchmark_search_quality_recall_by_mode(search_service, app_config):
    """Benchmark retrieval quality (hit/recall/MRR) for lexical and paraphrase query suites."""
    _skip_if_not_sqlite(app_config)
    _enable_semantic_for_benchmark(search_service, app_config)

    await _seed_benchmark_notes(search_service, note_count=240)

    for suite_name, cases in QUALITY_QUERY_SUITES.items():
        for mode in (
            SearchRetrievalMode.FTS,
            SearchRetrievalMode.VECTOR,
            SearchRetrievalMode.HYBRID,
        ):
            hits_at_1 = 0
            hits_at_5 = 0
            reciprocal_rank_sum = 0.0

            for case in cases:
                results = await search_service.search(
                    SearchQuery(
                        text=case.text,
                        retrieval_mode=mode,
                        entity_types=[SearchItemType.ENTITY],
                    ),
                    limit=10,
                )
                if not results:
                    continue

                relevant_rank = _first_relevant_rank(results, case.expected_topic, k=10)
                if relevant_rank is None:
                    continue

                reciprocal_rank_sum += 1.0 / relevant_rank
                if relevant_rank == 1:
                    hits_at_1 += 1
                if relevant_rank <= 5:
                    hits_at_5 += 1

            case_count = len(cases)
            hit_rate_at_1 = hits_at_1 / case_count
            recall_at_5 = hits_at_5 / case_count
            mrr_at_10 = reciprocal_rank_sum / case_count

            benchmark_name = f"quality recall ({suite_name}, {mode.value})"
            metrics = _print_quality_metrics(
                benchmark_name,
                cases=case_count,
                hit_rate_at_1=hit_rate_at_1,
                recall_at_5=recall_at_5,
                mrr_at_10=mrr_at_10,
            )
            _write_benchmark_artifact(benchmark_name, metrics)

            suite_env = suite_name.upper()
            mode_env = mode.value.upper()
            _enforce_min_threshold(
                metric_name=f"{suite_name}.{mode.value}.recall_at_5",
                actual=float(metrics["recall_at_5"]),
                env_var=f"BASIC_MEMORY_BENCH_MIN_{suite_env}_{mode_env}_RECALL_AT_5",
            )
            _enforce_min_threshold(
                metric_name=f"{suite_name}.{mode.value}.mrr_at_10",
                actual=float(metrics["mrr_at_10"]),
                env_var=f"BASIC_MEMORY_BENCH_MIN_{suite_env}_{mode_env}_MRR_AT_10",
            )
