"""Repository-owned cleanup for accepted-note vector search rows."""

from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository.project_repository import _load_sqlite_vec_on_session
from basic_memory.runtime.storage import ProjectId


DELETE_PROJECT_INDEX_VECTOR_CHUNKS_SQL = text("""
    DELETE FROM search_vector_chunks
    WHERE project_id = :project_id
      AND entity_id IN :deleted_entity_ids
""").bindparams(bindparam("deleted_entity_ids", expanding=True))

SELECT_PROJECT_INDEX_SQLITE_VECTOR_TABLES_SQL = text("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
      AND name IN ('search_vector_chunks', 'search_vector_embeddings')
""")

SELECT_PROJECT_INDEX_POSTGRES_VECTOR_TABLES_SQL = text("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = ANY (current_schemas(false))
      AND table_name IN ('search_vector_chunks', 'search_vector_embeddings')
""")

DELETE_PROJECT_INDEX_SQLITE_VECTOR_EMBEDDINGS_SQL = text("""
    DELETE FROM search_vector_embeddings
    WHERE rowid IN (
        SELECT id
        FROM search_vector_chunks
        WHERE project_id = :project_id
          AND entity_id IN :deleted_entity_ids
    )
""").bindparams(bindparam("deleted_entity_ids", expanding=True))

DELETE_PROJECT_INDEX_POSTGRES_VECTOR_EMBEDDINGS_SQL = text("""
    DELETE FROM search_vector_embeddings
    WHERE chunk_id IN (
        SELECT id
        FROM search_vector_chunks
        WHERE project_id = :project_id
          AND entity_id IN :deleted_entity_ids
    )
""").bindparams(bindparam("deleted_entity_ids", expanding=True))


def project_index_session_dialect_name(session: AsyncSession) -> str:
    """Return the SQLAlchemy dialect name for project-index maintenance."""
    return session.get_bind().dialect.name


async def project_index_vector_table_names(session: AsyncSession) -> frozenset[str]:
    """Return available vector table names for the current database backend."""
    dialect_name = project_index_session_dialect_name(session)
    if dialect_name == "sqlite":
        result = await session.execute(SELECT_PROJECT_INDEX_SQLITE_VECTOR_TABLES_SQL)
    elif dialect_name == "postgresql":
        result = await session.execute(SELECT_PROJECT_INDEX_POSTGRES_VECTOR_TABLES_SQL)
    else:
        raise RuntimeError(f"Unsupported project-index database dialect: {dialect_name}")

    return frozenset(str(table_name) for table_name in result.scalars())


async def delete_project_index_vector_rows(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_ids: Sequence[int],
) -> None:
    """Delete backend vector rows for project-index entity deletes when tables exist."""
    deleted_entity_ids = tuple(entity_ids)
    if not deleted_entity_ids:
        return

    vector_table_names = await project_index_vector_table_names(session)
    if "search_vector_chunks" not in vector_table_names:
        return

    delete_params = {
        "project_id": project_id,
        "deleted_entity_ids": deleted_entity_ids,
    }
    if "search_vector_embeddings" in vector_table_names:
        dialect_name = project_index_session_dialect_name(session)
        if dialect_name == "sqlite":
            if await _load_sqlite_vec_on_session(session):
                await session.execute(
                    DELETE_PROJECT_INDEX_SQLITE_VECTOR_EMBEDDINGS_SQL,
                    delete_params,
                )
        elif dialect_name == "postgresql":
            await session.execute(
                DELETE_PROJECT_INDEX_POSTGRES_VECTOR_EMBEDDINGS_SQL,
                delete_params,
            )
        else:
            raise RuntimeError(f"Unsupported project-index database dialect: {dialect_name}")

    await session.execute(DELETE_PROJECT_INDEX_VECTOR_CHUNKS_SQL, delete_params)
