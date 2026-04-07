"""Persist vector sync fingerprints on chunk metadata.

Revision ID: m6h7i8j9k0l1
Revises: l5g6h7i8j9k0
Create Date: 2026-04-07 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m6h7i8j9k0l1"
down_revision: Union[str, None] = "l5g6h7i8j9k0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add entity fingerprint + embedding model metadata to Postgres chunk rows.

    Trigger: vector sync now fast-skips unchanged entities using persisted
    semantic fingerprints.
    Why: chunk rows already own the per-entity derived metadata we diff against,
    so persisting the fingerprint on that table avoids a second sync-state table.
    Outcome: existing rows get empty-string placeholders and will be refreshed on
    the next vector sync before they become eligible for skip checks.
    """
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute(
        """
        ALTER TABLE search_vector_chunks
        ADD COLUMN IF NOT EXISTS entity_fingerprint TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE search_vector_chunks
        ADD COLUMN IF NOT EXISTS embedding_model TEXT
        """
    )
    op.execute(
        """
        UPDATE search_vector_chunks
        SET entity_fingerprint = COALESCE(entity_fingerprint, ''),
            embedding_model = COALESCE(embedding_model, '')
        """
    )
    op.execute(
        """
        ALTER TABLE search_vector_chunks
        ALTER COLUMN entity_fingerprint SET NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE search_vector_chunks
        ALTER COLUMN embedding_model SET NOT NULL
        """
    )


def downgrade() -> None:
    """Remove vector sync fingerprint columns from Postgres chunk rows."""
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute(
        """
        ALTER TABLE search_vector_chunks
        DROP COLUMN IF EXISTS embedding_model
        """
    )
    op.execute(
        """
        ALTER TABLE search_vector_chunks
        DROP COLUMN IF EXISTS entity_fingerprint
        """
    )
