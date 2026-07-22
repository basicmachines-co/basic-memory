"""Tests for ProjectService.get_embedding_status()."""

import os
from unittest.mock import patch

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
    create_embedding_provider,
)
from basic_memory.repository.semantic_vector_index_factory import (
    resolve_semantic_vector_index_name,
    semantic_embedding_identity,
)
from basic_memory.schemas.project_info import EmbeddingStatus
from basic_memory.services.project_service import ProjectService


def _is_postgres() -> bool:
    return os.environ.get("BASIC_MEMORY_TEST_POSTGRES", "").lower() in ("1", "true", "yes")


async def _execute(project_service: ProjectService, query, params=None):
    async with db.scoped_session(project_service.session_maker) as session:
        return await project_service.repository.execute_query(session, query, params or {})


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


@pytest.mark.parametrize(
    "config",
    [
        BasicMemoryConfig(),
        BasicMemoryConfig(
            semantic_embedding_provider="openai",
            semantic_embedding_model="text-embedding-3-large",
            semantic_embedding_dimensions=1024,
        ),
        BasicMemoryConfig(
            semantic_embedding_provider="litellm",
            semantic_embedding_model="cohere/embed-english-v3.0",
            semantic_embedding_dimensions=1024,
            semantic_embedding_document_prefix="passage: ",
            semantic_embedding_query_prefix="query: ",
        ),
    ],
)
def test_configured_embedding_identity_matches_runtime_provider(
    config: BasicMemoryConfig,
) -> None:
    """Status identity must exactly match the provider used by vector sync."""
    assert configured_embedding_provider_identity(config) == semantic_embedding_identity(
        create_embedding_provider(config)
    )


@pytest.mark.asyncio
async def test_embedding_status_does_not_construct_provider(
    project_service: ProjectService,
    test_project,
) -> None:
    """Project status should remain a metadata query, not provider initialization."""
    with (
        patch.object(
            type(project_service),
            "config_manager",
            new_callable=lambda: property(
                lambda self: _config_manager_with(semantic_search_enabled=True)
            ),
        ),
        patch(
            "basic_memory.repository.embedding_provider_factory.create_embedding_provider",
            side_effect=AssertionError("status must not construct an embedding provider"),
        ),
    ):
        status = await project_service.get_embedding_status(test_project.id)

    assert status.semantic_search_enabled is True


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
    await _execute(project_service, text(drop_sql), {})

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
    assert "Vector manifest not initialized" in (status.reindex_reason or "")


@pytest.mark.asyncio
async def test_embedding_status_entities_without_chunks(
    project_service: ProjectService, test_graph, test_project
):
    """When entities have search_index rows but no chunks, recommend reindex."""
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
    """When a manifest row is pending, recommend reindex."""
    # Get a real entity_id from the test graph
    entity_result = await _execute(
        project_service,
        text("SELECT id FROM entity WHERE project_id = :project_id LIMIT 1"),
        {"project_id": test_project.id},
    )
    entity_id = entity_result.scalar()

    await _insert_manifest_chunk(
        project_service,
        entity_id=entity_id,
        project_id=test_project.id,
        chunk_key="chunk-1",
        embedding_status="pending",
    )

    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=True)
        ),
    ):
        status = await project_service.get_embedding_status(test_project.id)

    assert status.vector_tables_exist is True
    assert status.total_chunks == 1
    assert status.orphaned_chunks == 1
    assert status.reindex_recommended is True
    assert "need vector indexing" in (status.reindex_reason or "")


@pytest.mark.asyncio
async def test_embedding_status_counts_only_current_ready_manifest_rows(
    project_service: ProjectService, test_graph, test_project
):
    """Status is backend-neutral and treats pending or mismatched rows as stale."""
    entity_result = await _execute(
        project_service,
        text("SELECT id FROM entity WHERE project_id = :project_id LIMIT 1"),
        {"project_id": test_project.id},
    )
    entity_id = entity_result.scalar()

    await _insert_manifest_chunk(
        project_service,
        entity_id=entity_id,
        project_id=test_project.id,
        chunk_key="ready",
    )
    await _insert_manifest_chunk(
        project_service,
        entity_id=entity_id,
        project_id=test_project.id,
        chunk_key="pending",
        embedding_status="pending",
    )
    await _insert_manifest_chunk(
        project_service,
        entity_id=entity_id,
        project_id=test_project.id,
        chunk_key="wrong-index",
        vector_index="milvus",
    )
    await _insert_manifest_chunk(
        project_service,
        entity_id=entity_id,
        project_id=test_project.id,
        chunk_key="wrong-model",
        embedding_identity="OtherProvider:other:384",
    )

    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(
            lambda self: _config_manager_with(semantic_search_enabled=True)
        ),
    ):
        with patch.object(
            project_service.repository,
            "scalar_vec_query",
            side_effect=AssertionError("status must not query vector storage"),
        ):
            status = await project_service.get_embedding_status(test_project.id)

    assert status.semantic_search_enabled is True
    assert status.vector_tables_exist is True
    assert status.total_chunks == 4
    assert status.total_embeddings == 1
    assert status.orphaned_chunks == 3
    assert status.reindex_recommended is True
    assert "pending or stale" in (status.reindex_reason or "")


@pytest.mark.asyncio
async def test_embedding_status_healthy(project_service: ProjectService, test_graph, test_project):
    """When all entities have embeddings, no reindex recommended."""
    # Clear any leftover data from prior tests
    await _execute(project_service, text("DELETE FROM search_vector_chunks"), {})

    # Insert a current, ready manifest row for every search_index entity.
    entity_result = await _execute(
        project_service,
        text("SELECT DISTINCT entity_id FROM search_index WHERE project_id = :project_id"),
        {"project_id": test_project.id},
    )
    entity_ids = [row[0] for row in entity_result.fetchall()]

    chunk_id = 1
    for eid in entity_ids:
        await _insert_manifest_chunk(
            project_service,
            entity_id=eid,
            project_id=test_project.id,
            chunk_key=f"chunk-{chunk_id}",
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
    await _execute(
        project_service,
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
    real_indexed_result = await _execute(
        project_service,
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


async def _insert_manifest_chunk(
    project_service: ProjectService,
    *,
    entity_id: int,
    project_id: int,
    chunk_key: str,
    vector_index: str | None = None,
    embedding_identity: str | None = None,
    embedding_status: str = "ready",
) -> None:
    """Insert one manifest row with explicit backend and readiness identity."""
    config = _config_manager_with(semantic_search_enabled=True).config
    active_vector_index = resolve_semantic_vector_index_name(config, config.database_backend)
    active_embedding_identity = configured_embedding_provider_identity(config)
    await _execute(
        project_service,
        text(
            "INSERT INTO search_vector_chunks "
            "(entity_id, project_id, chunk_key, chunk_text, source_hash, "
            "entity_fingerprint, embedding_model, vector_index, embedding_status) "
            "VALUES (:entity_id, :project_id, :chunk_key, 'test text', 'hash', "
            "'fingerprint', :embedding_identity, :vector_index, :embedding_status)"
        ),
        {
            "entity_id": entity_id,
            "project_id": project_id,
            "chunk_key": chunk_key,
            "embedding_identity": embedding_identity or active_embedding_identity,
            "vector_index": vector_index or active_vector_index,
            "embedding_status": embedding_status,
        },
    )
