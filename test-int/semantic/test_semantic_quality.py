"""Semantic search quality benchmarks across backend×provider combinations.

Runs identical query suites (lexical + paraphrase) against five configurations:

  sqlite-fts          SQLite FTS5, no embeddings
  sqlite-fastembed    SQLite + FastEmbed (384-d ONNX)
  postgres-fts        Postgres tsvector, no embeddings
  postgres-fastembed  Postgres + FastEmbed (384-d)
  postgres-openai     Postgres + OpenAI (1536-d, needs OPENAI_API_KEY)

Quality is measured via hit@1, recall@5, and MRR@10.  A comparison table
is printed at the end, and JSON-lines artifacts are written when
``BASIC_MEMORY_BENCHMARK_OUTPUT`` is set.
"""

from __future__ import annotations

import time

import pytest

from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode

from semantic.conftest import (
    ALL_COMBOS,
    SearchCombo,
    create_search_service,
    skip_if_needed,
    _create_fastembed_provider,
    _create_openai_provider,
)
from semantic.corpus import QUERY_SUITES, seed_benchmark_notes
from semantic.metrics import (
    QualityMetrics,
    first_relevant_rank,
    format_comparison_table,
    write_benchmark_artifact,
)


# --- Thresholds (conservative, tighten over time) ---
# Keys: (combo.name, suite_name, mode)
# Only combos/suites we have strong expectations for are listed.

RECALL_AT_5_THRESHOLDS: dict[tuple[str, str, str], float] = {
    # FTS-only: realistic corpus makes pure keyword matching harder
    ("sqlite-fts", "lexical", "fts"): 0.25,
    ("postgres-fts", "lexical", "fts"): 0.25,
    # FastEmbed hybrid should improve on FTS for both suites
    ("sqlite-fastembed", "lexical", "hybrid"): 0.37,
    ("sqlite-fastembed", "paraphrase", "hybrid"): 0.25,
    ("postgres-fastembed", "lexical", "hybrid"): 0.37,
    ("postgres-fastembed", "paraphrase", "hybrid"): 0.25,
    # OpenAI hybrid should handle paraphrases better than FastEmbed
    ("postgres-openai", "lexical", "hybrid"): 0.37,
    ("postgres-openai", "paraphrase", "hybrid"): 0.25,
}


def _resolve_provider(combo: SearchCombo):
    """Build the embedding provider for a combo, or None for FTS-only."""
    if combo.provider_name == "fastembed":
        return _create_fastembed_provider()
    if combo.provider_name == "openai":
        return _create_openai_provider()
    return None


def _retrieval_modes(combo: SearchCombo) -> list[SearchRetrievalMode]:
    """Return the retrieval modes to test for a given combo.

    FTS-only combos: [FTS]
    Semantic combos: [FTS, VECTOR, HYBRID]
    """
    if combo.provider_name is None:
        return [SearchRetrievalMode.FTS]
    return [SearchRetrievalMode.FTS, SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID]


# --- Parameterized test ---


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
@pytest.mark.parametrize("combo", ALL_COMBOS, ids=[c.name for c in ALL_COMBOS])
async def test_semantic_quality(
    combo: SearchCombo,
    sqlite_engine_factory,
    postgres_engine_factory,
    tmp_path,
):
    """Benchmark search quality for a single backend×provider combo."""
    skip_if_needed(combo)

    # Pick the right engine factory
    from basic_memory.config import DatabaseBackend

    if combo.backend == DatabaseBackend.SQLITE:
        engine_factory_result = sqlite_engine_factory
    else:
        if postgres_engine_factory is None:
            pytest.skip("Postgres engine not available")
        engine_factory_result = postgres_engine_factory

    provider = _resolve_provider(combo)
    search_service = await create_search_service(
        engine_factory_result, combo, tmp_path, embedding_provider=provider
    )

    # Seed corpus
    entities = await seed_benchmark_notes(search_service, note_count=240)
    assert len(entities) == 240

    # Collect metrics for each (suite, mode)
    all_metrics: list[QualityMetrics] = []

    for suite_name, cases in QUERY_SUITES.items():
        for mode in _retrieval_modes(combo):
            metrics = QualityMetrics(
                combo=combo.name,
                suite=suite_name,
                mode=mode.value,
            )

            for case in cases:
                t0 = time.perf_counter()
                results = await search_service.search(
                    SearchQuery(
                        text=case.text,
                        retrieval_mode=mode,
                        entity_types=[SearchItemType.ENTITY],
                    ),
                    limit=10,
                )
                latency = time.perf_counter() - t0

                rank = first_relevant_rank(results, case.expected_topic, k=10) if results else None
                metrics.record(case.text, case.expected_topic, rank, latency=latency)

            all_metrics.append(metrics)

    # Print comparison table
    table = format_comparison_table(all_metrics)
    print(f"\n{table}")

    # Write JSON artifact
    write_benchmark_artifact(all_metrics)

    # Enforce thresholds
    for m in all_metrics:
        threshold_key = (m.combo, m.suite, m.mode)
        threshold = RECALL_AT_5_THRESHOLDS.get(threshold_key)
        if threshold is not None:
            assert m.recall_at_5 >= threshold, (
                f"recall@5 for {m.combo}/{m.suite}/{m.mode}: {m.recall_at_5:.3f} < {threshold:.3f}"
            )
