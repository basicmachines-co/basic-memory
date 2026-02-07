"""Add Postgres semantic vector search tables (pgvector-aware, optional)

Revision ID: h1b2c3d4e5f6
Revises: d7e8f9a0b1c2
Create Date: 2026-02-07 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# Default embedding dimensions (fastembed bge-small-en-v1.5).
# pgvector HNSW indexes require fixed dimensions on the column.
DEFAULT_VECTOR_DIMS = 384

# revision identifiers, used by Alembic.
revision: str = "h1b2c3d4e5f6"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _pg_extension_is_available(connection, extension_name: str) -> bool:
    result = connection.execute(
        text(
            "SELECT EXISTS (  SELECT 1 FROM pg_available_extensions WHERE name = :extension_name)"
        ),
        {"extension_name": extension_name},
    )
    return bool(result.scalar())


def upgrade() -> None:
    """Create Postgres vector chunk/embedding tables when pgvector is available.

    Trigger: database backend is PostgreSQL and pgvector package is installed.
    Why: semantic indexing is optional; upgrades should not fail for deployments
    without pgvector.
    Outcome: creates derived vector tables + indexes for semantic search.
    """
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    if not _pg_extension_is_available(connection, "vector"):
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS search_vector_chunks (
            id BIGSERIAL PRIMARY KEY,
            entity_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            chunk_key TEXT NOT NULL,
            chunk_text TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (project_id, entity_id, chunk_key)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_search_vector_chunks_project_entity
        ON search_vector_chunks (project_id, entity_id)
        """
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS search_vector_embeddings (
            chunk_id BIGINT PRIMARY KEY
                REFERENCES search_vector_chunks(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL,
            embedding vector({DEFAULT_VECTOR_DIMS}) NOT NULL,
            embedding_dims INTEGER NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_search_vector_embeddings_project_dims
        ON search_vector_embeddings (project_id, embedding_dims)
        """
    )

    # HNSW index for approximate nearest-neighbour search.
    # Without this every vector query is a sequential scan.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_search_vector_embeddings_hnsw
        ON search_vector_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    """Remove Postgres vector chunk/embedding tables.

    Does not drop pgvector extension because other schema objects may depend on it.
    """
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute("DROP TABLE IF EXISTS search_vector_embeddings")
    op.execute("DROP TABLE IF EXISTS search_vector_chunks")
