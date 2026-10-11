"""Index vector chunks by the search row they were cut from.

A filtered vector search hands the index the filter as SQL over the chunk manifest and
admits chunks by their search row: ``(project_id, source_type, source_row_id)`` (#1746).
Without an index on those columns, every filtered search read the whole chunk table
(373,000 rows in the largest production project) to find the admitted chunks.

SQLite creates its chunk table and indexes at runtime in the search repository, which
creates this index too.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-11 15:30:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the chunk location index on Postgres."""
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_search_vector_chunks_location
        ON search_vector_chunks (project_id, source_type, source_row_id)
        """
    )


def downgrade() -> None:
    """Drop the chunk location index on Postgres."""
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS idx_search_vector_chunks_location")
