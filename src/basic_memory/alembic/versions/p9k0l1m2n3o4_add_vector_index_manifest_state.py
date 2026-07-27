"""Add vector index identity and readiness to the semantic manifest.

Revision ID: p9k0l1m2n3o4
Revises: o8j9k0l1m2n3
Create Date: 2026-07-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "p9k0l1m2n3o4"
down_revision: Union[str, None] = "o8j9k0l1m2n3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_note_file_vacate_table() -> None:
    """Backfill the canonical o8 schema when the duplicate vector revision ran."""
    op.create_table(
        "note_file_vacate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_checksum", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "file_path",
            name="uix_note_file_vacate_project_file_path",
        ),
    )
    op.create_index(
        "ix_note_file_vacate_project_id",
        "note_file_vacate",
        ["project_id"],
        unique=False,
    )


def upgrade() -> None:
    """Reconcile both historical o8 schemas, then add vector manifest state.

    For a short period, note-file vacates and vector manifest state shared the
    o8 revision ID. A database can therefore arrive here with either schema
    while Alembic reports the same predecessor. Restore the canonical o8
    note-file table first, then idempotently apply the PostgreSQL vector state.
    SQLite creates its derived vector table lazily at runtime, so it only needs
    the missing canonical table repaired.
    """
    connection = op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())

    if "note_file_vacate" not in tables:
        _create_note_file_vacate_table()

    if connection.dialect.name != "postgresql":
        return
    if "search_vector_chunks" not in tables:
        return

    op.execute("ALTER TABLE search_vector_chunks ADD COLUMN IF NOT EXISTS vector_index TEXT")
    op.execute("ALTER TABLE search_vector_chunks ADD COLUMN IF NOT EXISTS embedding_status TEXT")
    op.execute(
        """
        UPDATE search_vector_chunks
        SET vector_index = 'pgvector'
        WHERE vector_index IS NULL
        """
    )

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
            WHERE embedding_status IS NULL
            """
        )
    else:
        op.execute(
            """
            UPDATE search_vector_chunks
            SET embedding_status = 'pending'
            WHERE embedding_status IS NULL
            """
        )

    op.execute("ALTER TABLE search_vector_chunks ALTER COLUMN vector_index SET NOT NULL")
    op.execute("ALTER TABLE search_vector_chunks ALTER COLUMN embedding_status SET NOT NULL")
    constraint_names = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints("search_vector_chunks")
    }
    if "ck_search_vector_chunks_embedding_status" not in constraint_names:
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
