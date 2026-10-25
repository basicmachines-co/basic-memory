"""SQLite index recovery against real files and sqlite-vec, without application mocks."""

import json
from importlib import import_module
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from basic_memory import db
from basic_memory.repository.semantic_vector_index import VectorDeletion, VectorKey
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.repository.sqlite_vec_index import SQLiteVecIndex
from basic_memory.services.search_service import SearchService


pytestmark = pytest.mark.real_embedder


@pytest.mark.asyncio
async def test_large_vector_delete_preserves_other_generations_and_projects(
    search_service: SearchService,
) -> None:
    repository = search_service.repository
    if not isinstance(repository, SQLiteSearchRepository):
        pytest.skip("SQLite expression-depth limits are backend-specific.")
    index = repository._semantic_vector_index
    assert isinstance(index, SQLiteVecIndex)
    await index.initialize()
    project_id = repository.project_id
    embedding = json.dumps([1.0] + [0.0] * (index.scope.dimensions - 1))
    count = 1201
    # Put protected rows at batch boundaries as well as in the final partial batch.
    ready = {199, 400}
    newer = {200, 1200}
    other_project = {399}
    protected = ready | newer | other_project
    async with db.scoped_session(repository.session_maker) as session:
        await index._ensure_loaded(session)
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks "
                "(id, entity_id, project_id, chunk_key, source_type, source_row_id, "
                "chunk_index, chunk_text, source_hash, entity_fingerprint, embedding_model, "
                "vector_index, embedding_status) VALUES "
                "(:id, 1850, :project_id, :chunk_key, 'entity', 1850, :id, 'text', "
                ":source_hash, 'fingerprint', :model, 'sqlite-vec', :status)"
            ),
            [
                {
                    "id": i + 1,
                    "project_id": project_id + 1 if i in other_project else project_id,
                    "chunk_key": f"chunk:{i}",
                    "source_hash": "new" if i in newer else "old",
                    "model": index.scope.embedding_identity,
                    "status": "ready" if i in ready else "pending",
                }
                for i in range(count)
            ],
        )
        await session.execute(
            text(
                "INSERT INTO search_vector_embeddings "
                "(rowid, project_id, embedding, source_hash) "
                "VALUES (:id, :project_id, :embedding, :source_hash)"
            ),
            [
                {
                    "id": i + 1,
                    "project_id": project_id + 1 if i in other_project else project_id,
                    "embedding": embedding,
                    "source_hash": "new" if i in newer else "old",
                }
                for i in range(count)
            ],
        )
        await session.commit()

    deletions = [
        VectorDeletion(key=VectorKey(entity_id=1850, chunk_key=f"chunk:{i}"), source_hash="old")
        for i in range(count)
    ]
    await index.delete(project_id, deletions)
    await index.delete(project_id, deletions)

    async with db.scoped_session(repository.session_maker) as session:
        await index._ensure_loaded(session)
        chunks = await session.scalars(text("SELECT id FROM search_vector_chunks ORDER BY id"))
        vectors = await session.scalars(
            text("SELECT rowid FROM search_vector_embeddings ORDER BY rowid")
        )
        expected = sorted(i + 1 for i in protected)
        assert chunks.all() == expected
        assert vectors.all() == expected


@pytest.mark.parametrize("upgrade", [True, False])
@pytest.mark.parametrize("scratch_populated", [True, False])
def test_fts_rebuild_retries_with_committed_scratch_table(
    tmp_path: Path, upgrade: bool, scratch_populated: bool
) -> None:
    migration = import_module(
        "basic_memory.alembic.versions.d2e3f4a5b6c7_add_script_ngrams_to_full_text_search"
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    try:
        # Commit the interrupted state first, then reopen it as a later startup would.
        with engine.begin() as connection:
            columns = migration.SQLITE_COLUMNS.format(
                script_column="" if upgrade else "script_ngrams,"
            )
            connection.exec_driver_sql(f"CREATE VIRTUAL TABLE search_index USING fts5({columns})")
            connection.exec_driver_sql(
                "INSERT INTO search_index (id, title, content_stems, project_id) "
                "VALUES (1, 'Preserved note', 'surviving words', 7)"
            )
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE search_index_rebuilt USING fts5(stale)"
            )
            if scratch_populated:
                connection.exec_driver_sql(
                    "INSERT INTO search_index_rebuilt (stale) VALUES ('obsolete scratch data')"
                )

        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                if upgrade:
                    migration.upgrade()
                else:
                    migration.downgrade()

        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT id, title, project_id FROM search_index WHERE search_index MATCH 'surviving'"
            ).all() == [(1, "Preserved note", 7)]
            columns = connection.exec_driver_sql("PRAGMA table_info(search_index)")
            assert ("script_ngrams" in {row[1] for row in columns}) == upgrade
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name = 'search_index_rebuilt'"
                ).all()
                == []
            )
    finally:
        engine.dispose()
