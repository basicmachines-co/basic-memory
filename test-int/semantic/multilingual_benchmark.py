"""Typed benchmark cases, measurements, and artifacts for multilingual retrieval."""

from __future__ import annotations

import json
import math
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Literal, Sequence

import fastembed
import psutil
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from basic_memory.config import BasicMemoryConfig, DatabaseBackend, default_fastembed_cache_dir
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.schemas.search import SearchRetrievalMode

from semantic.multilingual_corpus import MultilingualQuery, RetrievalCaseKind


@dataclass(frozen=True, slots=True)
class EmbeddingModelCase:
    """One FastEmbed model contract to benchmark in an isolated process."""

    key: str
    model_name: str
    dimensions: int
    cache_repository: str
    catalog_size_gb: float
    license: str
    document_prefix: str | None = None
    query_prefix: str | None = None


FASTEMBED_MODEL_CASES = (
    EmbeddingModelCase(
        key="bge-small-en",
        model_name="BAAI/bge-small-en-v1.5",
        dimensions=384,
        cache_repository="qdrant/bge-small-en-v1.5-onnx-q",
        catalog_size_gb=0.067,
        license="mit",
    ),
    EmbeddingModelCase(
        key="multilingual-minilm",
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        dimensions=384,
        cache_repository="qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
        catalog_size_gb=0.22,
        license="apache-2.0",
    ),
    EmbeddingModelCase(
        key="multilingual-mpnet",
        model_name="sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        dimensions=768,
        cache_repository="xenova/paraphrase-multilingual-mpnet-base-v2",
        catalog_size_gb=1.0,
        license="apache-2.0",
    ),
    EmbeddingModelCase(
        key="multilingual-e5-large",
        model_name="intfloat/multilingual-e5-large",
        dimensions=1024,
        cache_repository="qdrant/multilingual-e5-large-onnx",
        catalog_size_gb=2.24,
        license="mit",
        document_prefix="passage: ",
        query_prefix="query: ",
    ),
)


@dataclass(frozen=True, slots=True)
class BenchmarkStorageCase:
    """One database and vector-index pairing exercised by the benchmark."""

    key: str
    database_backend: DatabaseBackend
    vector_index_name: Literal["sqlite-vec", "pgvector", "milvus"]


BENCHMARK_STORAGE_CASES = (
    BenchmarkStorageCase("sqlite", DatabaseBackend.SQLITE, "sqlite-vec"),
    BenchmarkStorageCase("postgres", DatabaseBackend.POSTGRES, "pgvector"),
    BenchmarkStorageCase("milvus", DatabaseBackend.POSTGRES, "milvus"),
)


def embedding_model_case(key: str) -> EmbeddingModelCase:
    """Return one configured model case or fail with the supported keys."""
    for model_case in FASTEMBED_MODEL_CASES:
        if model_case.key == key:
            return model_case
    supported = ", ".join(model.key for model in FASTEMBED_MODEL_CASES)
    raise ValueError(f"Unknown multilingual benchmark model {key!r}; choose one of: {supported}")


def benchmark_storage_case(value: str) -> BenchmarkStorageCase:
    """Parse a supported SQL database and vector-index pairing."""
    normalized = value.strip().lower()
    for storage_case in BENCHMARK_STORAGE_CASES:
        if storage_case.key == normalized:
            return storage_case
    raise ValueError("Multilingual benchmark backend must be 'sqlite', 'postgres', or 'milvus'")


def benchmark_retrieval_mode(value: str) -> SearchRetrievalMode:
    """Parse the retrieval mode supported by the embedding benchmark."""
    try:
        mode = SearchRetrievalMode(value.strip().lower())
    except ValueError as exc:
        raise ValueError("Multilingual benchmark mode must be 'vector' or 'hybrid'") from exc
    if mode not in {SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID}:
        raise ValueError("Multilingual benchmark mode must be 'vector' or 'hybrid'")
    return mode


def embedding_benchmark_config(
    model_case: EmbeddingModelCase,
    storage_case: BenchmarkStorageCase,
    *,
    milvus_uri: str | None = None,
) -> BasicMemoryConfig:
    """Build the Cloud-like FastEmbed configuration used by benchmark services."""
    if storage_case.vector_index_name == "milvus" and milvus_uri is None:
        raise ValueError("The Milvus benchmark requires a local or remote Milvus URI")
    configured_vector_index: Literal["pgvector", "milvus"] = (
        "milvus" if storage_case.vector_index_name == "milvus" else "pgvector"
    )

    return BasicMemoryConfig(
        env="test",
        database_backend=storage_case.database_backend,
        semantic_search_enabled=True,
        semantic_vector_index=configured_vector_index,
        semantic_embedding_provider="fastembed",
        semantic_embedding_model=model_case.model_name,
        semantic_embedding_dimensions=model_case.dimensions,
        semantic_embedding_document_prefix=model_case.document_prefix,
        semantic_embedding_query_prefix=model_case.query_prefix,
        semantic_embedding_batch_size=8,
        semantic_embedding_sync_batch_size=64,
        semantic_embedding_threads=1,
        semantic_embedding_parallel=1,
        semantic_min_similarity=0.55,
        milvus_uri=milvus_uri,
    )


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    """The ranked and production-threshold outcomes for one query."""

    query: MultilingualQuery
    ranked_permalinks: tuple[str, ...]
    accepted_permalinks: tuple[str, ...]
    ranking_latency_seconds: float
    accepted_latency_seconds: float

    @property
    def first_relevant_rank(self) -> int | None:
        relevant = set(self.query.relevant_permalinks)
        for rank, permalink in enumerate(self.ranked_permalinks[:10], start=1):
            if permalink in relevant:
                return rank
        return None


@dataclass(frozen=True, slots=True)
class RetrievalSummary:
    """Aggregate retrieval quality for a named corpus slice."""

    name: str
    query_count: int
    positive_query_count: int
    negative_query_count: int
    recall_at_5: float
    mrr_at_10: float
    accepted_empty_rate: float
    wrong_top_rate: float
    negative_false_positive_rate: float
    ranking_p50_ms: float
    ranking_p95_ms: float
    accepted_p50_ms: float
    accepted_p95_ms: float

    def as_metrics(self) -> dict[str, float | int]:
        """Return numeric metrics compatible with the existing JSONL comparator."""
        return {
            "queries_executed": self.query_count,
            "positive_queries": self.positive_query_count,
            "negative_queries": self.negative_query_count,
            "recall_at_5": round(self.recall_at_5, 4),
            "mrr_at_10": round(self.mrr_at_10, 4),
            "accepted_empty_rate": round(self.accepted_empty_rate, 4),
            "wrong_top_rate": round(self.wrong_top_rate, 4),
            "negative_false_positive_rate": round(self.negative_false_positive_rate, 4),
            "ranking_p50_ms": round(self.ranking_p50_ms, 2),
            "ranking_p95_ms": round(self.ranking_p95_ms, 2),
            "accepted_p50_ms": round(self.accepted_p50_ms, 2),
            "accepted_p95_ms": round(self.accepted_p95_ms, 2),
        }


@dataclass(frozen=True, slots=True)
class RuntimeMeasurements:
    """Operational measurements captured by one isolated model/backend run."""

    document_count: int
    cold_load_seconds: float
    indexing_seconds: float
    rss_before_load_bytes: int
    rss_after_load_bytes: int
    rss_after_index_bytes: int
    peak_rss_bytes: int
    model_cache_bytes: int
    vector_storage_bytes: int

    def as_metrics(self) -> dict[str, float | int]:
        """Return comparable numeric runtime metrics."""
        documents_per_second = (
            self.document_count / self.indexing_seconds if self.indexing_seconds else 0.0
        )
        return {
            "documents_indexed": self.document_count,
            "cold_load_seconds": round(self.cold_load_seconds, 4),
            "indexing_seconds": round(self.indexing_seconds, 4),
            "documents_per_sec": round(documents_per_second, 4),
            "rss_before_load_bytes": self.rss_before_load_bytes,
            "rss_after_load_bytes": self.rss_after_load_bytes,
            "rss_model_delta_bytes": max(0, self.rss_after_load_bytes - self.rss_before_load_bytes),
            "rss_after_index_bytes": self.rss_after_index_bytes,
            "peak_rss_bytes": self.peak_rss_bytes,
            "model_cache_bytes": self.model_cache_bytes,
            "vector_storage_bytes": self.vector_storage_bytes,
        }


def result_permalinks(results: Sequence[SearchIndexRow]) -> tuple[str, ...]:
    """Return result permalinks while preserving rank order."""
    return tuple(result.permalink for result in results if result.permalink is not None)


def percentile_ms(samples: Sequence[float], quantile: float) -> float:
    """Return a linearly interpolated percentile in milliseconds."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    position = (len(ordered) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index] * 1000
    fraction = position - lower_index
    value = ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction
    return value * 1000


def summarize_retrieval(
    name: str,
    observations: Sequence[RetrievalObservation],
) -> RetrievalSummary:
    """Compute quality, threshold, and latency metrics for one corpus slice."""
    positive = [item for item in observations if item.query.relevant_permalinks]
    negative = [item for item in observations if not item.query.relevant_permalinks]

    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    for observation in positive:
        relevant = set(observation.query.relevant_permalinks)
        retrieved = set(observation.ranked_permalinks[:5])
        recalls.append(len(relevant.intersection(retrieved)) / len(relevant))
        rank = observation.first_relevant_rank
        reciprocal_ranks.append(1.0 / rank if rank is not None else 0.0)

    accepted_empty_rate = (
        sum(not item.accepted_permalinks for item in positive) / len(positive) if positive else 0.0
    )
    wrong_top_rate = (
        sum(
            not item.ranked_permalinks
            or item.ranked_permalinks[0] not in item.query.relevant_permalinks
            for item in positive
        )
        / len(positive)
        if positive
        else 0.0
    )
    false_positive_rate = (
        sum(bool(item.accepted_permalinks) for item in negative) / len(negative)
        if negative
        else 0.0
    )
    ranking_latencies = [item.ranking_latency_seconds for item in observations]
    accepted_latencies = [item.accepted_latency_seconds for item in observations]

    return RetrievalSummary(
        name=name,
        query_count=len(observations),
        positive_query_count=len(positive),
        negative_query_count=len(negative),
        recall_at_5=mean(recalls) if recalls else 0.0,
        mrr_at_10=mean(reciprocal_ranks) if reciprocal_ranks else 0.0,
        accepted_empty_rate=accepted_empty_rate,
        wrong_top_rate=wrong_top_rate,
        negative_false_positive_rate=false_positive_rate,
        ranking_p50_ms=percentile_ms(ranking_latencies, 0.50),
        ranking_p95_ms=percentile_ms(ranking_latencies, 0.95),
        accepted_p50_ms=percentile_ms(accepted_latencies, 0.50),
        accepted_p95_ms=percentile_ms(accepted_latencies, 0.95),
    )


def retrieval_summaries(
    observations: Sequence[RetrievalObservation],
) -> tuple[RetrievalSummary, ...]:
    """Build overall, behavior, and language slices from one benchmark run."""
    summaries = [summarize_retrieval("overall", observations)]
    for kind in RetrievalCaseKind:
        kind_observations = [item for item in observations if item.query.kind is kind]
        if kind_observations:
            summaries.append(summarize_retrieval(kind.value, kind_observations))
    for language in sorted({item.query.language for item in observations}):
        language_observations = [item for item in observations if item.query.language == language]
        summaries.append(summarize_retrieval(f"language-{language}", language_observations))
    return tuple(summaries)


def process_rss_bytes() -> int:
    """Return the process's current resident memory."""
    return psutil.Process().memory_info().rss


def process_peak_rss_bytes(*, platform_name: str = sys.platform) -> int:
    """Return the process high-water resident memory across supported platforms."""
    if platform_name == "win32":
        return int(psutil.Process().memory_info().peak_wset)

    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if platform_name == "darwin" else peak * 1024)


def model_cache_size_bytes(
    model_case: EmbeddingModelCase,
    benchmark_config: BasicMemoryConfig,
) -> int:
    """Measure the selected model's materialized FastEmbed cache subtree."""
    configured_cache = benchmark_config.semantic_embedding_cache_dir
    cache_root = Path(configured_cache or default_fastembed_cache_dir())
    model_root = cache_root / f"models--{model_case.cache_repository.replace('/', '--')}"
    return directory_size_bytes(model_root)


def directory_size_bytes(directory: Path) -> int:
    """Measure unique file bytes without double-counting Hugging Face hardlinks."""
    if not directory.exists():
        return 0
    seen_files: set[tuple[int, int]] = set()
    total = 0
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in seen_files:
            continue
        seen_files.add(identity)
        total += stat.st_size
    return total


async def vector_storage_size_bytes(
    engine: AsyncEngine,
    storage_case: BenchmarkStorageCase,
    *,
    milvus_storage_directory: Path | None = None,
) -> int:
    """Measure database storage after the corpus vectors have been indexed."""
    if storage_case.vector_index_name == "milvus":
        if milvus_storage_directory is None:
            raise ValueError("Milvus storage measurement requires its local storage directory")
        return directory_size_bytes(milvus_storage_directory)

    if storage_case.database_backend is DatabaseBackend.SQLITE:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT COALESCE(SUM(pgsize), 0) FROM dbstat "
                    "WHERE name = 'search_vector_chunks' "
                    "OR name IN ("
                    "SELECT name FROM sqlite_schema WHERE tbl_name = 'search_vector_chunks'"
                    ") "
                    "OR name LIKE 'search_vector_embeddings%' "
                    "OR name IN ("
                    "SELECT name FROM sqlite_schema "
                    "WHERE tbl_name LIKE 'search_vector_embeddings%'"
                    ")"
                )
            )
            return int(result.scalar_one())

    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT pg_total_relation_size('search_vector_chunks') + "
                "pg_total_relation_size('search_vector_embeddings')"
            )
        )
        return int(result.scalar_one())


def benchmark_context(
    model_case: EmbeddingModelCase,
    storage_case: BenchmarkStorageCase,
    retrieval_mode: SearchRetrievalMode,
    corpus_version: str,
    threshold: float,
) -> dict[str, object]:
    """Describe the software, model, and host context for an artifact."""
    return {
        "model_key": model_case.key,
        "model_name": model_case.model_name,
        "dimensions": model_case.dimensions,
        "document_prefix": model_case.document_prefix,
        "query_prefix": model_case.query_prefix,
        "license": model_case.license,
        "catalog_size_gb": model_case.catalog_size_gb,
        "backend": storage_case.key,
        "database_backend": storage_case.database_backend.value,
        "vector_index": storage_case.vector_index_name,
        "retrieval_mode": retrieval_mode.value,
        "corpus_version": corpus_version,
        "production_similarity_threshold": threshold,
        "python_version": platform.python_version(),
        "fastembed_version": fastembed.__version__,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpu_count": psutil.cpu_count(),
        "physical_memory_bytes": psutil.virtual_memory().total,
    }


def write_multilingual_benchmark_artifact(
    output_path: Path,
    context: dict[str, object],
    summaries: Sequence[RetrievalSummary],
    runtime: RuntimeMeasurements,
    observations: Sequence[RetrievalObservation],
) -> None:
    """Write comparison-compatible JSONL records for one isolated run."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    observation_payload = [
        {
            "query": item.query.name,
            "kind": item.query.kind.value,
            "language": item.query.language,
            "relevant": list(item.query.relevant_permalinks),
            "ranked": list(item.ranked_permalinks),
            "accepted": list(item.accepted_permalinks),
            "first_relevant_rank": item.first_relevant_rank,
        }
        for item in observations
    ]

    with output_path.open("a", encoding="utf-8") as artifact:
        for summary in summaries:
            payload: dict[str, object] = {
                "benchmark": f"multilingual-quality-{summary.name}",
                "timestamp_utc": timestamp,
                "context": context,
                "metrics": summary.as_metrics(),
            }
            if summary.name == "overall":
                payload["observations"] = observation_payload
            artifact.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

        artifact.write(
            json.dumps(
                {
                    "benchmark": "multilingual-runtime",
                    "timestamp_utc": timestamp,
                    "context": context,
                    "metrics": runtime.as_metrics(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
