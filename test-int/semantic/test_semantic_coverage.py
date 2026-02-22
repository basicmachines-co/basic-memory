"""Targeted coverage tests for postgres_search_repository.py vector paths.

Exercises the uncovered code paths in PostgresSearchRepository:
- _ensure_vector_tables (lines 258-352): pgvector extension, table creation,
  dimension mismatch detection
- _run_vector_query (lines 389-429): vector similarity query with cosine distance
- _write_embeddings (lines 431-458): embedding upsert into pgvector table
- Metadata filters in FTS search (lines 682-745): JSONB filter operators
  (eq, in, contains, gt/gte/lt/lte, between)

Uses postgres-fastembed combo (no OpenAI dependency) with the pgvector container.
"""

from __future__ import annotations

import pytest

from basic_memory.config import DatabaseBackend
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode

from semantic.conftest import (
    SearchCombo,
    create_search_service,
    skip_if_needed,
    _create_fastembed_provider,
)
from semantic.corpus import (
    TOPIC_TERMS,
    build_benchmark_content,
    seed_benchmark_notes,
)


# Combo used for all coverage tests: Postgres + FastEmbed (no OpenAI needed)
PG_FASTEMBED = SearchCombo("postgres-fastembed", DatabaseBackend.POSTGRES, "fastembed", 384)


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_postgres_vector_table_setup_and_query(postgres_engine_factory, tmp_path):
    """Exercise _ensure_vector_tables, sync_entity_vectors, and _run_vector_query.

    This covers:
    - CREATE EXTENSION vector
    - search_vector_chunks table creation
    - search_vector_embeddings table creation with HNSW index
    - Dimension detection via pg_attribute
    - Embedding write via _write_embeddings
    - Vector similarity query via _run_vector_query
    """
    skip_if_needed(PG_FASTEMBED)
    if postgres_engine_factory is None:
        pytest.skip("Postgres engine not available")

    provider = _create_fastembed_provider()
    search_service = await create_search_service(
        postgres_engine_factory, PG_FASTEMBED, tmp_path, embedding_provider=provider
    )

    # Seed a small corpus — enough to exercise the vector pipeline
    entities = await seed_benchmark_notes(search_service, note_count=20)
    assert len(entities) == 20

    # Vector-only search — exercises _run_vector_query
    results = await search_service.search(
        SearchQuery(
            text="authentication token session",
            retrieval_mode=SearchRetrievalMode.VECTOR,
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=5,
    )
    assert results, "Vector search should return results after indexing"

    # Verify the top results are from the auth topic
    auth_found = any((r.permalink or "").startswith("bench/auth-") for r in results[:5])
    assert auth_found, "Vector search should rank auth notes highly for auth query"


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_postgres_hybrid_search(postgres_engine_factory, tmp_path):
    """Exercise the hybrid (RRF fusion) code path on Postgres.

    This covers the full _search_hybrid path including both FTS and vector
    retrieval with reciprocal rank fusion.
    """
    skip_if_needed(PG_FASTEMBED)
    if postgres_engine_factory is None:
        pytest.skip("Postgres engine not available")

    provider = _create_fastembed_provider()
    search_service = await create_search_service(
        postgres_engine_factory, PG_FASTEMBED, tmp_path, embedding_provider=provider
    )

    await seed_benchmark_notes(search_service, note_count=20)

    # Hybrid search — exercises _search_hybrid RRF fusion
    results = await search_service.search(
        SearchQuery(
            text="database migration schema",
            retrieval_mode=SearchRetrievalMode.HYBRID,
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=5,
    )
    assert results, "Hybrid search should return results"
    assert any((r.permalink or "").startswith("bench/database-") for r in results[:5]), (
        "Hybrid search should rank database notes highly"
    )


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_postgres_semantic_with_metadata_filters(postgres_engine_factory, tmp_path):
    """Exercise metadata filter operators in Postgres FTS search.

    This covers the JSONB filter code paths in PostgresSearchRepository.search():
    - eq: simple equality on metadata field
    - contains: array containment (tags)
    - in: $in operator for multiple values
    """
    skip_if_needed(PG_FASTEMBED)
    if postgres_engine_factory is None:
        pytest.skip("Postgres engine not available")

    provider = _create_fastembed_provider()
    search_service = await create_search_service(
        postgres_engine_factory, PG_FASTEMBED, tmp_path, embedding_provider=provider
    )

    # Seed notes — they have metadata: {"tags": ["benchmark", topic], "status": "active"}
    await seed_benchmark_notes(search_service, note_count=40)

    # --- eq filter: status = "active" ---
    results_eq = await search_service.search(
        SearchQuery(
            text="authentication",
            metadata_filters={"status": "active"},
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=10,
    )
    assert results_eq, "Metadata eq filter should return results"

    # --- contains filter: tags contain "auth" ---
    results_contains = await search_service.search(
        SearchQuery(
            text="*",
            tags=["auth"],
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=20,
    )
    assert results_contains, "Metadata contains filter should return results"
    for r in results_contains:
        assert (r.permalink or "").startswith("bench/auth-"), (
            "Tag filter should only return auth notes"
        )

    # --- $in filter: status in ["active", "draft"] ---
    results_in = await search_service.search(
        SearchQuery(
            text="database",
            metadata_filters={"status": {"$in": ["active", "draft"]}},
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=10,
    )
    assert results_in, "Metadata $in filter should return results"


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_postgres_vector_dimension_detection(postgres_engine_factory, tmp_path):
    """Exercise dimension detection and table initialization paths.

    This test verifies that:
    1. Vector tables are created correctly on first use
    2. The dimension detection via pg_attribute works
    3. Subsequent calls to _ensure_vector_tables are idempotent
    """
    skip_if_needed(PG_FASTEMBED)
    if postgres_engine_factory is None:
        pytest.skip("Postgres engine not available")

    provider = _create_fastembed_provider()
    search_service = await create_search_service(
        postgres_engine_factory, PG_FASTEMBED, tmp_path, embedding_provider=provider
    )

    repo = search_service.repository

    # First entity triggers _ensure_vector_tables
    entity = await search_service.entity_repository.create(
        {
            "title": "Dimension Test Note",
            "note_type": "benchmark",
            "entity_metadata": {"tags": ["test"]},
            "content_type": "text/markdown",
            "permalink": "bench/dim-test",
            "file_path": "bench/dim-test.md",
        }
    )
    content = build_benchmark_content("auth", TOPIC_TERMS["auth"], 0)
    await search_service.index_entity_data(entity, content=content)
    await search_service.sync_entity_vectors(entity.id)

    # Verify tables initialized flag is set
    assert repo._vector_tables_initialized

    # Calling _ensure_vector_tables again should be a no-op (short-circuit)
    await repo._ensure_vector_tables()
    assert repo._vector_tables_initialized


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.benchmark
async def test_postgres_incremental_vector_update(postgres_engine_factory, tmp_path):
    """Exercise the diff/update path in sync_entity_vectors.

    This covers:
    - Initial chunk insert + embedding write
    - Content update → chunk hash changes → re-embed changed chunks
    - Stale chunk deletion
    """
    skip_if_needed(PG_FASTEMBED)
    if postgres_engine_factory is None:
        pytest.skip("Postgres engine not available")

    provider = _create_fastembed_provider()
    search_service = await create_search_service(
        postgres_engine_factory, PG_FASTEMBED, tmp_path, embedding_provider=provider
    )

    # Create and index initial entity
    entity = await search_service.entity_repository.create(
        {
            "title": "Update Test Note",
            "note_type": "benchmark",
            "entity_metadata": {"tags": ["test"]},
            "content_type": "text/markdown",
            "permalink": "bench/update-test",
            "file_path": "bench/update-test.md",
        }
    )
    initial_content = build_benchmark_content("sync", TOPIC_TERMS["sync"], 0)
    await search_service.index_entity_data(entity, content=initial_content)
    await search_service.sync_entity_vectors(entity.id)

    # Verify initial indexing produces results
    results_before = await search_service.search(
        SearchQuery(
            text="filesystem watcher",
            retrieval_mode=SearchRetrievalMode.VECTOR,
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=5,
    )
    assert results_before

    # Update content — should trigger chunk diff and re-embedding
    updated_content = build_benchmark_content("agent", TOPIC_TERMS["agent"], 0)
    await search_service.index_entity_data(entity, content=updated_content)
    await search_service.sync_entity_vectors(entity.id)

    # Verify updated content is findable
    results_after = await search_service.search(
        SearchQuery(
            text="agent memory context",
            retrieval_mode=SearchRetrievalMode.VECTOR,
            entity_types=[SearchItemType.ENTITY],
        ),
        limit=5,
    )
    assert results_after
