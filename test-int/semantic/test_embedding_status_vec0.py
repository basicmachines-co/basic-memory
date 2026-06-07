"""Integration regression test for get_embedding_status against a real vec0 table.

Regression for #658: after a successful `bm reindex --embeddings`, `bm project info`
still reported "sqlite-vec is unavailable", "Indexed 0/N", and "Chunks 0", and
recommended an unnecessary reindex.

Root cause: get_embedding_status() ran the vec0 JOIN count queries on a bare pooled
ProjectRepository session that never loaded the sqlite-vec extension, so SQLite raised
"no such module: vec0", which the except block mis-reported as "unavailable".

This test exercises the real failure path: it builds a REAL vec0 virtual table, writes a
real embedding into it via the search repository, then queries get_embedding_status through
a ProjectRepository session that did NOT pre-load the extension (mirroring the bug). The
healthy unit test substitutes a plain regular table for vec0 and therefore does not cover
this path.
"""

import os
import sqlite3

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
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
    """get_embedding_status must report a populated real vec0 table as healthy.

    Before the fix, the vec0 JOIN ran on a session without sqlite-vec loaded and
    raised "no such module: vec0", which the except block mapped to
    vector_tables_exist=False + reindex_recommended=True.
    """
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
    entity_repo = EntityRepository(session_maker, project_id=project_id)
    entity = await entity_repo.create(
        {
            "title": "Vec Note",
            "note_type": "note",
            "content_type": "text/markdown",
            "project_id": project_id,
            "permalink": "vec-note",
            "file_path": "vec-note.md",
        }
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

    # --- Insert a chunk + a real embedding into the vec0 table ---
    # _write_embeddings writes the embedding into the vec0 virtual table keyed by
    # rowid == chunk id, exactly like the reindex path.
    async with db.scoped_session(session_maker) as session:
        await search_repo._ensure_sqlite_vec_loaded(session)
        chunk_result = await session.execute(
            text(
                "INSERT INTO search_vector_chunks "
                "(entity_id, project_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model) "
                "VALUES (:eid, :pid, 'chunk-1', 'vec content', 'hash', "
                "'fp-hash', 'bge-small-en-v1.5') "
                "RETURNING id"
            ),
            {"eid": entity_id, "pid": project_id},
        )
        chunk_id = chunk_result.scalar_one()

        await search_repo._write_embeddings(
            session,
            [(chunk_id, "vec content")],
            [_unit_vector(dimensions)],
        )
        await session.commit()

    # Evict the vec-loaded connection from the pool. sqlite-vec is loaded
    # per-connection, so disposing forces get_embedding_status onto a brand-new
    # connection that never loaded the extension — exactly the #658 bug condition
    # (e.g. a fresh `bm project info` process after `bm reindex --embeddings`).
    await _engine.dispose()

    # --- Query status through a fresh ProjectRepository (no extension preloaded) ---
    project_repository = ProjectRepository(session_maker)
    project_service = ProjectService(project_repository)

    status = await project_service.get_embedding_status(project_id)

    assert status.semantic_search_enabled is True
    # The vec0 JOIN must succeed, so the table is reported as present and healthy.
    assert status.vector_tables_exist is True
    assert status.reindex_recommended is False
    assert status.reindex_reason is None
    # Counts must reflect the real data, not the false "0" from the unavailable path.
    assert status.total_indexed_entities == 1
    assert status.total_chunks == 1
    assert status.total_entities_with_chunks == 1
    assert status.total_embeddings == 1
    assert status.orphaned_chunks == 0
