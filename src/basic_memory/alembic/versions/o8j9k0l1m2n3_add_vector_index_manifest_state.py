"""Add vector index identity and readiness to the semantic manifest.

Revision ID: o8j9k0l1m2n3
Revises: n7i8j9k0l1m2
Create Date: 2026-07-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "o8j9k0l1m2n3"
down_revision: Union[str, None] = "n7i8j9k0l1m2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make PostgreSQL chunk rows an authoritative vector-write manifest.

    SQLite creates this derived table lazily at runtime. Its schema check
    rebuilds older vector tables on first semantic initialization, so only the
    migration-owned PostgreSQL table needs an in-place upgrade here.
    """
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    if "search_vector_chunks" not in inspect(connection).get_table_names():
        return

    op.execute("ALTER TABLE search_vector_chunks ADD COLUMN IF NOT EXISTS vector_index TEXT")
    op.execute("ALTER TABLE search_vector_chunks ADD COLUMN IF NOT EXISTS embedding_status TEXT")
    op.execute("UPDATE search_vector_chunks SET vector_index = 'pgvector'")

    tables = set(inspect(connection).get_table_names())
    if "search_vector_embeddings" in tables:
        op.execute(
            """
            UPDATE search_vector_chunks AS chunks
            SET embedding_status = CASE
                WHEN EXISTS (
                    SELECT 1 FROM search_vector_embeddings AS embeddings
                    WHERE embeddings.chunk_id = chunks.id
                ) THEN 'ready'
                ELSE 'pending'
            END
            """
        )
    else:
        op.execute("UPDATE search_vector_chunks SET embedding_status = 'pending'")

    op.execute("ALTER TABLE search_vector_chunks ALTER COLUMN vector_index SET NOT NULL")
    op.execute("ALTER TABLE search_vector_chunks ALTER COLUMN embedding_status SET NOT NULL")
    op.create_check_constraint(
        "ck_search_vector_chunks_embedding_status",
        "search_vector_chunks",
        "embedding_status IN ('pending', 'ready')",
    )


def downgrade() -> None:
    """Remove vector index manifest state from PostgreSQL."""
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    if "search_vector_chunks" not in inspect(connection).get_table_names():
        return

    op.drop_constraint(
        "ck_search_vector_chunks_embedding_status",
        "search_vector_chunks",
        type_="check",
    )
    op.execute("ALTER TABLE search_vector_chunks DROP COLUMN IF EXISTS embedding_status")
    op.execute("ALTER TABLE search_vector_chunks DROP COLUMN IF EXISTS vector_index")
