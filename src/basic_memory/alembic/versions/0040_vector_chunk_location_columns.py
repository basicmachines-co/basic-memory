"""Key vector chunks by content and store their search row location in columns.

A chunk key used to embed the search_index row id it was cut from
("observation:95276:0"). Updating a note deletes and recreates its observations and
relations, and Postgres never reuses ids, so every rewrite gave every observation and
relation chunk a new key. Nothing matched the previous manifest and the whole note was
re-embedded on each edit: about 86 chunks per edit for an agent maintaining large ledger
notes (basic-memory-cloud tenant 0fffb994, 2026-10-08).

The row id and chunk position move into ``source_type``, ``source_row_id`` and
``chunk_index``, and the key becomes ``<type>:<sha256 of the chunk text>:<occurrence>``.
Built-in pgvector embeddings are stored by chunk row id, so re-keying in place keeps
every existing embedding: nothing is re-embedded by this migration.

Revision ID: 0040
Revises: z9a0b1c2d3e4
Create Date: 2026-10-08 19:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "0040"
down_revision: Union[str, None] = "z9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Split chunk location into columns and re-key Postgres chunks by content.

    SQLite chunk tables are created at runtime by the search repository, which
    applies the same upgrade (``SQLITE_CHUNK_LOCATION_UPGRADE``) when it finds them.
    """
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute(
        """
        ALTER TABLE search_vector_chunks
        ADD COLUMN IF NOT EXISTS source_type TEXT,
        ADD COLUMN IF NOT EXISTS source_row_id INTEGER,
        ADD COLUMN IF NOT EXISTS chunk_index INTEGER
        """
    )
    op.execute(
        """
        UPDATE search_vector_chunks SET
            source_type = split_part(chunk_key, ':', 1),
            source_row_id = split_part(chunk_key, ':', 2)::integer,
            chunk_index = split_part(chunk_key, ':', 3)::integer
        WHERE source_type IS NULL
        """
    )
    # Old keys ("type:id:n") and new keys ("type:<64 hex>:n") cannot collide, so the
    # unique (project_id, entity_id, chunk_key) constraint holds throughout.
    op.execute(
        """
        UPDATE search_vector_chunks AS chunks
        SET chunk_key = ranked.source_type || ':' || ranked.source_hash || ':'
            || ranked.occurrence
        FROM (
            SELECT id, source_type, source_hash,
                ROW_NUMBER() OVER (
                    PARTITION BY project_id, entity_id, source_type, source_hash
                    ORDER BY source_row_id, chunk_index, id
                ) - 1 AS occurrence
            FROM search_vector_chunks
        ) AS ranked
        WHERE ranked.id = chunks.id
        """
    )
    # pgvector finds embeddings by chunk row id, so re-keying keeps them. An external
    # index (Milvus) stores the old key itself; re-embed those chunks.
    op.execute(
        """
        UPDATE search_vector_chunks SET embedding_status = 'pending'
        WHERE vector_index NOT IN ('', 'sqlite-vec', 'pgvector')
        """
    )
    op.execute(
        """
        ALTER TABLE search_vector_chunks
        ALTER COLUMN source_type SET NOT NULL,
        ALTER COLUMN source_row_id SET NOT NULL,
        ALTER COLUMN chunk_index SET NOT NULL
        """
    )


def downgrade() -> None:
    """Restore row-id chunk keys and drop the location columns."""
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute(
        """
        UPDATE search_vector_chunks
        SET chunk_key = source_type || ':' || source_row_id || ':' || chunk_index
        """
    )
    op.execute(
        """
        ALTER TABLE search_vector_chunks
        DROP COLUMN IF EXISTS chunk_index,
        DROP COLUMN IF EXISTS source_row_id,
        DROP COLUMN IF EXISTS source_type
        """
    )
