"""Tests for ProjectService.get_embedding_status()."""

import os
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SAOperationalError

from basic_memory.schemas.project_info import EmbeddingStatus
from basic_memory.services.project_service import ProjectService


def _is_postgres() -> bool:
    return os.environ.get("BASIC_MEMORY_TEST_POSTGRES", "").lower() in ("1", "true", "yes")


@pytest.mark.asyncio
async def test_embedding_status_semantic_disabled(project_service: ProjectService, test_project):
    """When semantic search is disabled, return minimal status with zero counts."""
    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=False)
        ),
    ):
        status = await project_service.get_embedding_status(test_project.id)

    assert isinstance(status, EmbeddingStatus)
    assert status.semantic_search_enabled is False
    assert status.reindex_recommended is False
    assert status.total_chunks == 0
    assert status.total_embeddings == 0


@pytest.mark.asyncio
async def test_embedding_status_vector_tables_missing(
    project_service: ProjectService, test_graph, test_project
):
    """When vector tables don't exist, recommend reindex."""
    # Drop the chunks table created by the fixture to simulate missing vector tables
    # Postgres requires CASCADE (due to index dependencies); SQLite doesn't support it
    drop_sql = (
        "DROP TABLE IF EXISTS search_vector_chunks CASCADE"
        if _is_postgres()
        else "DROP TABLE IF EXISTS search_vector_chunks"
    )
    await project_service.repository.execute_query(text(drop_sql), {})

    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=True)
        ),
    ):
        status = await project_service.get_embedding_status(test_project.id)

    assert status.semantic_search_enabled is True
    assert status.embedding_provider == "fastembed"
    assert status.embedding_model == "bge-small-en-v1.5"
    assert status.vector_tables_exist is False
    assert status.reindex_recommended is True
    assert "Vector tables not initialized" in (status.reindex_reason or "")


@pytest.mark.asyncio
async def test_embedding_status_entities_without_chunks(
    project_service: ProjectService, test_graph, test_project
):
    """When entities have search_index rows but no chunks, recommend reindex."""
    # search_vector_chunks table is created by the test fixture (empty)
    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=True)
        ),
    ):
        status = await project_service.get_embedding_status(test_project.id)

    assert status.semantic_search_enabled is True
    assert status.vector_tables_exist is True
    # test_graph creates entities indexed in search_index, but no vector chunks
    assert status.total_indexed_entities > 0
    assert status.total_chunks == 0
    assert status.reindex_recommended is True
    assert "never been built" in (status.reindex_reason or "")


@pytest.mark.asyncio
async def test_embedding_status_orphaned_chunks(
    project_service: ProjectService, test_graph, test_project
):
    """When chunks exist without matching embeddings, recommend reindex."""
    # Insert a chunk row (no matching embedding = orphan)
    # Get a real entity_id from the test graph
    entity_result = await project_service.repository.execute_query(
        text("SELECT id FROM entity WHERE project_id = :project_id LIMIT 1"),
        {"project_id": test_project.id},
    )
    entity_id = entity_result.scalar()

    await project_service.repository.execute_query(
        text(
            "INSERT INTO search_vector_chunks "
            "("
            "entity_id, project_id, chunk_key, chunk_text, source_hash, "
            "entity_fingerprint, embedding_model"
            ") "
            "VALUES ("
            ":entity_id, :project_id, 'chunk-1', 'test text', 'abc123', "
            "'fp-abc123', 'bge-small-en-v1.5'"
            ")"
        ),
        {"entity_id": entity_id, "project_id": test_project.id},
    )

    # Create a minimal search_vector_embeddings stub (not a real vector table)
    # so the LEFT JOIN works and finds the orphan.
    # Uses chunk_id as PK — Postgres queries join on chunk_id,
    # SQLite queries join on rowid which aliases INTEGER PRIMARY KEY.
    await project_service.repository.execute_query(
        text(
            "CREATE TABLE IF NOT EXISTS search_vector_embeddings (  chunk_id INTEGER PRIMARY KEY)"
        ),
        {},
    )

    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=True)
        ),
    ):
        status = await project_service.get_embedding_status(test_project.id)

    # Clean up stub table to avoid polluting subsequent tests
    await project_service.repository.execute_query(
        text("DROP TABLE IF EXISTS search_vector_embeddings"), {}
    )

    assert status.vector_tables_exist is True
    assert status.total_chunks == 1
    assert status.orphaned_chunks == 1
    assert status.reindex_recommended is True
    assert "orphaned chunks" in (status.reindex_reason or "")


@pytest.mark.asyncio
async def test_embedding_status_handles_sqlite_vec_unavailable(
    project_service: ProjectService, test_graph, test_project
):
    """Unreadable vec0 tables should degrade to unavailable status instead of crashing."""
    # Trigger: Postgres test matrix executes the same unit suite.
    # Why: sqlite-vec loading failures are specific to SQLite virtual tables, not Postgres joins.
    # Outcome: keep the regression focused on the backend that can actually hit this path.
    if _is_postgres():
        pytest.skip("sqlite-vec unavailable handling is SQLite-specific.")

    original_execute_query = project_service.repository.execute_query

    async def _execute_query_with_vec0_failure(query, params):
        query_text = str(query)
        if "JOIN search_vector_embeddings" in query_text:
            raise SAOperationalError(query_text, params, Exception("no such module: vec0"))
        return await original_execute_query(query, params)

    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=True)
        ),
    ):
        with patch.object(
            project_service.repository,
            "execute_query",
            side_effect=_execute_query_with_vec0_failure,
        ):
            status = await project_service.get_embedding_status(test_project.id)

    assert status.semantic_search_enabled is True
    assert status.total_indexed_entities > 0
    assert status.vector_tables_exist is False
    assert status.reindex_recommended is True
    assert "sqlite-vec is unavailable" in (status.reindex_reason or "")


@pytest.mark.asyncio
async def test_embedding_status_healthy(project_service: ProjectService, test_graph, test_project):
    """When all entities have embeddings, no reindex recommended."""
    # Clear any leftover data from prior tests
    await project_service.repository.execute_query(text("DELETE FROM search_vector_chunks"), {})

    # Drop any existing virtual table (may have been created by search_service init)
    # and recreate as a simple regular table for testing the join logic.
    # Uses chunk_id as PK — Postgres queries join on chunk_id,
    # SQLite queries join on rowid which aliases INTEGER PRIMARY KEY.
    await project_service.repository.execute_query(
        text("DROP TABLE IF EXISTS search_vector_embeddings"), {}
    )
    await project_service.repository.execute_query(
        text("CREATE TABLE search_vector_embeddings (  chunk_id INTEGER PRIMARY KEY)"),
        {},
    )

    # Insert a chunk + matching embedding for every search_index entity
    entity_result = await project_service.repository.execute_query(
        text("SELECT DISTINCT entity_id FROM search_index WHERE project_id = :project_id"),
        {"project_id": test_project.id},
    )
    entity_ids = [row[0] for row in entity_result.fetchall()]

    chunk_id = 1
    for eid in entity_ids:
        await project_service.repository.execute_query(
            text(
                "INSERT INTO search_vector_chunks "
                "("
                "id, entity_id, project_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model"
                ") "
                "VALUES ("
                ":id, :entity_id, :project_id, :key, 'text', 'hash', "
                "'fp-hash', 'bge-small-en-v1.5'"
                ")"
            ),
            {
                "id": chunk_id,
                "entity_id": eid,
                "project_id": test_project.id,
                "key": f"chunk-{chunk_id}",
            },
        )
        await project_service.repository.execute_query(
            text("INSERT INTO search_vector_embeddings (chunk_id) VALUES (:chunk_id)"),
            {"chunk_id": chunk_id},
        )
        chunk_id += 1

    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=True)
        ),
    ):
        status = await project_service.get_embedding_status(test_project.id)

    # Clean up stub table to avoid polluting subsequent tests
    await project_service.repository.execute_query(
        text("DROP TABLE IF EXISTS search_vector_embeddings"), {}
    )

    assert status.vector_tables_exist is True
    assert status.total_chunks > 0
    assert status.total_embeddings == status.total_chunks
    assert status.orphaned_chunks == 0
    assert status.reindex_recommended is False
    assert status.reindex_reason is None


@pytest.mark.asyncio
async def test_embedding_status_excludes_stale_entity_ids(
    project_service: ProjectService, test_graph, test_project
):
    """Stale rows in search_index for deleted entities should not inflate counts.

    Regression test for #670: after reindex, project info reported missing embeddings
    because stale entity_ids in search_index/search_vector_chunks inflated total_indexed_entities.
    """
    # Insert a stale search_index row for an entity_id that doesn't exist in the entity table.
    # Include 'id' column — required NOT NULL on Postgres (regular table),
    # ignored on SQLite (FTS5 virtual table where id is UNINDEXED).
    stale_entity_id = 999999
    await project_service.repository.execute_query(
        text(
            "INSERT INTO search_index "
            "(id, entity_id, project_id, type, title, permalink, content_stems, "
            "content_snippet, file_path, metadata) "
            "VALUES (:id, :eid, :pid, 'entity', 'Stale Note', 'stale-note', "
            "'stale content', 'stale snippet', 'stale.md', '{}')"
        ),
        {"id": stale_entity_id, "eid": stale_entity_id, "pid": test_project.id},
    )

    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=True)
        ),
    ):
        status = await project_service.get_embedding_status(test_project.id)

    # The stale entity_id should NOT be counted in total_indexed_entities.
    # Count real entities that have search_index rows (the stale one should be excluded).
    real_indexed_result = await project_service.repository.execute_query(
        text(
            "SELECT COUNT(DISTINCT si.entity_id) FROM search_index si "
            "JOIN entity e ON e.id = si.entity_id "
            "WHERE si.project_id = :pid"
        ),
        {"pid": test_project.id},
    )
    real_indexed_count = real_indexed_result.scalar() or 0

    # Exact match — stale entity_id must not inflate the count
    assert status.total_indexed_entities == real_indexed_count


@pytest.mark.asyncio
async def test_get_project_info_includes_embedding_status(
    project_service: ProjectService, test_graph, test_project
):
    """get_project_info() response includes embedding_status field."""
    info = await project_service.get_project_info(test_project.name)
    assert info.embedding_status is not None
    assert isinstance(info.embedding_status, EmbeddingStatus)


# --- Helper ---


def _config_manager_with(semantic_search_enabled: bool):
    """Create a ConfigManager whose config has the given semantic_search_enabled value."""
    from basic_memory.config import ConfigManager

    cm = ConfigManager()
    # Patch the config object in-place
    cm.config.semantic_search_enabled = semantic_search_enabled
    return cm
