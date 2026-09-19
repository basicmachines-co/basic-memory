"""Regression coverage for bounded sqlite-vec reconciliation work."""

from collections.abc import Callable
from typing import cast

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models.project import Project
from basic_memory.repository.semantic_vector_index import VectorIndexScope
from basic_memory.repository.sqlite_vec_index import SQLiteVecIndex


@pytest.mark.asyncio
async def test_sqlite_vec_reconciliation_is_bounded_by_project_manifest(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
) -> None:
    """A project reconciliation must not correlate every vector with every manifest row."""
    engine, session_maker = engine_factory
    index = SQLiteVecIndex(
        session_maker,
        VectorIndexScope(
            namespace="test",
            embedding_identity="stub:model",
            dimensions=4,
        ),
    )
    await index.initialize()

    manifest_rows = [
        {
            "id": row_id,
            "entity_id": row_id,
            "project_id": test_project.id,
            "chunk_key": f"entity:{row_id}:0",
            "embedding_status": "ready" if row_id <= 2_000 else "pending",
        }
        for row_id in range(1, 3_002)
    ]
    vector_rows = [
        {
            "rowid": row_id,
            "project_id": test_project.id,
            "embedding": "[1,0,0,0]",
        }
        for row_id in range(1, 3_003)
    ]
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "id, entity_id, project_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":id, :entity_id, :project_id, :chunk_key, 'text', 'hash', "
                "'fingerprint', 'stub:model', 'sqlite-vec', :embedding_status)"
            ),
            manifest_rows,
        )
        await session.execute(
            text(
                "INSERT INTO search_vector_embeddings "
                "(rowid, project_id, embedding, source_hash) "
                "VALUES (:rowid, :project_id, :embedding, 'hash')"
            ),
            vector_rows,
        )
        await session.commit()

    max_progress_calls = 2_000
    progress_calls = 0

    def interrupt_quadratic_plan() -> int:
        nonlocal progress_calls
        progress_calls += 1
        return int(progress_calls > max_progress_calls)

    async def set_progress_handler(handler: Callable[[], int] | None, steps: int) -> None:
        async with engine.connect() as connection:
            raw_connection = await connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection
            assert driver_connection is not None
            await driver_connection.set_progress_handler(handler, steps)

    await set_progress_handler(interrupt_quadratic_plan, 1_000)
    try:
        try:
            await index.delete_orphans(test_project.id, [])
        except SAOperationalError:
            pytest.fail("sqlite-vec reconciliation exceeded its bounded SQLite operation budget")
    finally:
        await set_progress_handler(None, 0)

    async with db.scoped_session(session_maker) as session:
        remaining = await session.execute(
            text("SELECT rowid FROM search_vector_embeddings ORDER BY rowid")
        )
        remaining_rowids = cast(list[int], remaining.scalars().all())

    assert remaining_rowids == list(range(1, 2_001))
    assert progress_calls <= max_progress_calls
