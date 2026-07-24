"""Integration regression test for manifest-backed status with a real vec0 table.

Regression for #658: after a successful `bm reindex --embeddings`, `bm project info`
still reported "sqlite-vec is unavailable", "Indexed 0/N", and "Chunks 0", and
recommended an unnecessary reindex.

Status now reads the authoritative SQL manifest instead of reopening backend-specific
vector storage. This test builds and writes a real vec0 table, then proves a fresh project
status connection can report the ready manifest without loading sqlite-vec.
"""

import os
import sqlite3
from unittest.mock import patch

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager, DatabaseBackend
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.semantic_vector_index import VectorKey, VectorRecord
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.services.project_service import ProjectService


def _is_postgres() -> bool:
    return os.environ.get("BASIC_MEMORY_TEST_POSTGRES", "").lower() in ("1", "true", "yes")


def _unit_vector(dimensions: int) -> list[float]:
    """Return a deterministic unit-norm vector for the vec0 embedding column."""
    # vec0 stores float[dimensions]; the actual values don't matter for the count
    # queries, but using a normalized vector keeps the row well-formed.
    vec = [0.0] * dimensions
    vec[0] = 1.0
    return vec


@pytest.mark.asyncio
async def test_embedding_status_reads_real_vec0_table(engine_factory, test_project, config_manager):
    """A ready vec0-backed manifest remains healthy on a fresh SQL connection."""
    # Trigger: Postgres test matrix executes the same suite.
    # Why: vec0 + per-connection sqlite-vec loading is SQLite-specific.
    # Outcome: keep the regression on the backend that can actually hit this path.
    if _is_postgres():
        pytest.skip("Real vec0 table handling is SQLite-specific.")

    # Trigger: Python build without SQLite extension loading (#711 — python.org
    # macOS / some Windows interpreters lack enable_load_extension).
    # Why: this test creates a REAL vec0 virtual table during setup, which is
    # impossible without loading the sqlite-vec extension.
    # Outcome: skip the regression as an environment-capability gap; the codebase
    # already degrades gracefully in that scenario (covered by the unit test).
    _probe = sqlite3.connect(":memory:")
    if not hasattr(_probe, "enable_load_extension"):
        _probe.close()
        pytest.skip(
            "Python build does not support SQLite extension loading — "
            "cannot create real vec0 tables"
        )
    _probe.close()

    _engine, session_maker = engine_factory
    project_id = test_project.id

    # --- Build a REAL vec0 table via the search repository ---
    # Semantic enabled with a fastembed provider so _ensure_vector_tables creates
    # the vec0-backed search_vector_embeddings table (float[384]).
    app_config = BasicMemoryConfig(
        env="test",
        database_backend=DatabaseBackend.SQLITE,
        semantic_search_enabled=True,
    )
    search_repo = SQLiteSearchRepository(
        session_maker,
        project_id=project_id,
        app_config=app_config,
    )
    await search_repo._ensure_vector_tables()
    dimensions = search_repo._vector_dimensions

    # --- Seed a real entity + search_index row so counts are non-zero ---
    # Use the repository so model-level defaults (external_id) are applied.
    entity_repo = EntityRepository(project_id=project_id)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repo.create(
            session,
            {
                "title": "Vec Note",
                "note_type": "note",
                "content_type": "text/markdown",
                "project_id": project_id,
                "permalink": "vec-note",
                "file_path": "vec-note.md",
            },
        )
    entity_id = entity.id

    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_index "
                "(id, entity_id, project_id, type, title, permalink, content_stems, "
                "content_snippet, file_path, metadata) "
                "VALUES (:id, :eid, :pid, 'entity', 'Vec Note', 'vec-note', "
                "'vec content', 'vec snippet', 'vec-note.md', '{}')"
            ),
            {"id": entity_id, "eid": entity_id, "pid": project_id},
        )
        await session.commit()

    # --- Insert a pending manifest row, then write the real vec0 value ---
    # The pending row commits first because the vector adapter owns a separate
    # transaction and must be able to resolve the stable key.
    async with db.scoped_session(session_maker) as session:
        chunk_result = await session.execute(
            text(
                "INSERT INTO search_vector_chunks "
                "(entity_id, project_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status) "
                "VALUES (:eid, :pid, 'chunk-1', 'vec content', 'hash', "
                "'fp-hash', :embedding_model, :vector_index, 'pending') "
                "RETURNING id"
            ),
            {
                "eid": entity_id,
                "pid": project_id,
                "embedding_model": search_repo._embedding_model_key(),
                "vector_index": search_repo._semantic_vector_index_name,
            },
        )
        chunk_id = chunk_result.scalar_one()
        await session.commit()

    await search_repo._semantic_vector_index.upsert(
        [
            VectorRecord(
                key=VectorKey(entity_id=entity_id, chunk_key="chunk-1"),
                values=tuple(_unit_vector(dimensions)),
            )
        ]
    )

    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("UPDATE search_vector_chunks SET embedding_status = 'ready' WHERE id = :chunk_id"),
            {"chunk_id": chunk_id},
        )
        await session.commit()

    # Evict the vec-loaded connection from the pool. sqlite-vec is loaded
    # per-connection, so disposing forces get_embedding_status onto a brand-new
    # connection that never loaded the extension — exactly the #658 bug condition
    # (e.g. a fresh `bm project info` process after `bm reindex --embeddings`).
    await _engine.dispose()

    # --- Query status through a fresh ProjectRepository (no extension preloaded) ---
    project_repository = ProjectRepository()
    project_service = ProjectService(project_repository, session_maker)

    # Test fixtures run with semantic search disabled; the status call reads the global
    # ConfigManager, so patch it to report semantic enabled for this regression path.
    def _config_manager_semantic_enabled() -> ConfigManager:
        cm = ConfigManager()
        cm.config.semantic_search_enabled = True
        return cm

    with patch.object(
        type(project_service),
        "config_manager",
        new_callable=lambda: property(lambda self: _config_manager_semantic_enabled()),
    ):
        status = await project_service.get_embedding_status(project_id)

    assert status.semantic_search_enabled is True
    # Status reads the ready manifest, so it never needs to reopen the vec0 table.
    assert status.vector_tables_exist is True
    assert status.reindex_recommended is False
    assert status.reindex_reason is None
    # Counts must reflect the real data, not the false "0" from the unavailable path.
    assert status.total_indexed_entities == 1
    assert status.total_chunks == 1
    assert status.total_entities_with_chunks == 1
    assert status.total_embeddings == 1
    assert status.orphaned_chunks == 0
