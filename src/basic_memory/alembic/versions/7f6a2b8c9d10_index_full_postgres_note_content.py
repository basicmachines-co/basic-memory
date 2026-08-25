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
        # --- Repair: recreate search_index if a destructive reindex removed it ---
        # Trigger: search_index does not exist. Through v0.23.0, the per-project
        # search reindex ran DROP TABLE IF EXISTS search_index, and on Postgres
        # nothing outside migrations recreates that table.
        # Why: the chunk backfill below reads search_index and the chunks table
        # declares a foreign key to it, so without repair this migration fails
        # permanently and blocks the database from ever reaching head.
        # Outcome: healthy databases no-op on IF NOT EXISTS; a repaired database
        # gets an empty index that the next full reindex repopulates. The DDL
        # mirrors the current schema in models/search.py.
        op.execute("""
            CREATE TABLE IF NOT EXISTS search_index (
                id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                title TEXT,
                content_stems TEXT,
                content_snippet TEXT,
                permalink VARCHAR,
                file_path VARCHAR,
                type VARCHAR,
                from_id INTEGER,
                to_id INTEGER,
                relation_type VARCHAR,
                entity_id INTEGER,
                category VARCHAR,
                metadata JSONB,
                created_at TIMESTAMP WITH TIME ZONE,
                updated_at TIMESTAMP WITH TIME ZONE,
                textsearchable_index_col tsvector GENERATED ALWAYS AS (
                    to_tsvector(
                        'english',
                        coalesce(title, '') || ' ' ||
                        coalesce(content_stems, '')
                    )
                ) STORED,
                PRIMARY KEY (id, type, project_id),
                FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
            )
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_search_index_project_id
            ON search_index (project_id)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_index_fts
            ON search_index USING gin(textsearchable_index_col)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_index_metadata_gin
            ON search_index USING gin(metadata jsonb_path_ops)
        """)
        op.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uix_search_index_permalink_project
            ON search_index (permalink, project_id)
            WHERE permalink IS NOT NULL
        """)
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
            -- PostgreSQL ignores lexemes at 2 KiB and above. Stepping 5,952
            -- characters leaves a conservative 2,048-character overlap, so
            -- every indexable lexeme split at one edge is complete in the next.
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
                (chunk_start - 1) / 5952,
                substring(search_index.content_snippet FROM chunk_start FOR 8000)
            FROM search_index
            CROSS JOIN LATERAL generate_series(
                1,
                length(search_index.content_snippet),
                5952
            ) AS chunk_start
            WHERE search_index.content_snippet IS NOT NULL
              AND search_index.content_snippet <> ''
        """)
        op.execute("""
            CREATE INDEX idx_search_index_fts_chunks_fts
            ON search_index_fts_chunks USING gin(textsearchable_index_col)
        """)


def downgrade() -> None:
    """Remove the bounded full-content PostgreSQL FTS chunks."""
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS search_index_fts_chunks")
