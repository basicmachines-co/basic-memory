"""Add Postgres semantic vector search tables (pgvector-aware, optional)

Revision ID: h1b2c3d4e5f6
Revises: d7e8f9a0b1c2
Create Date: 2026-02-07 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h1b2c3d4e5f6"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Postgres vector chunk metadata table.

    Trigger: database backend is PostgreSQL.
    Why: search_vector_chunks stores text metadata with no vector-dimension
    dependency, so it's safe in a migration.  search_vector_embeddings (which
    requires pgvector and a provider-specific dimension) is created at runtime
    by PostgresSearchRepository._ensure_vector_tables(), mirroring the SQLite
    pattern where vector tables are created dynamically.
    Outcome: creates the dimension-independent chunks table.  The embeddings
    table + HNSW index are deferred to runtime.
    """
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

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


def downgrade() -> None:
    """Remove Postgres vector chunk/embedding tables.

    Does not drop pgvector extension because other schema objects may depend on it.
    """
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute("DROP TABLE IF EXISTS search_vector_embeddings")
    op.execute("DROP TABLE IF EXISTS search_vector_chunks")
