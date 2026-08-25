"""Index complete PostgreSQL note content for full-text search.

Revision ID: 7f6a2b8c9d10
Revises: 2d26b287813b
Create Date: 2026-08-24 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7f6a2b8c9d10"
down_revision: Union[str, None] = "2d26b287813b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Index complete note bodies as bounded PostgreSQL FTS chunks."""
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("""
            CREATE TABLE search_index_fts_chunks (
                project_id INTEGER NOT NULL,
                search_index_id INTEGER NOT NULL,
                search_index_type VARCHAR NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                textsearchable_index_col tsvector GENERATED ALWAYS AS (
                    to_tsvector('english', chunk_text)
                ) STORED,
                PRIMARY KEY (project_id, search_index_id, search_index_type, chunk_index),
                FOREIGN KEY (search_index_id, search_index_type, project_id)
                    REFERENCES search_index(id, type, project_id)
                    ON UPDATE CASCADE ON DELETE CASCADE
            )
        """)
        op.execute("""
            CREATE INDEX idx_search_index_fts_chunks_fts
            ON search_index_fts_chunks USING gin(textsearchable_index_col)
        """)
        op.execute("""
            INSERT INTO search_index_fts_chunks (
                project_id,
                search_index_id,
                search_index_type,
                chunk_index,
                chunk_text
            )
            SELECT
                search_index.project_id,
                search_index.id,
                search_index.type,
                (chunk_start - 1) / 7800,
                substring(search_index.content_snippet FROM chunk_start FOR 8000)
            FROM search_index
            CROSS JOIN LATERAL generate_series(
                1,
                length(search_index.content_snippet),
                7800
            ) AS chunk_start
            WHERE search_index.content_snippet IS NOT NULL
              AND search_index.content_snippet <> ''
        """)


def downgrade() -> None:
    """Remove the bounded full-content PostgreSQL FTS chunks."""
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS search_index_fts_chunks")
