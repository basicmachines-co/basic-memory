"""Add CJK search token indexes

Revision ID: 8a7b6c5d4e3f
Revises: 7f6a2b8c9d10
Create Date: 2026-08-29 23:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


# revision identifiers, used by Alembic.
revision: str = "8a7b6c5d4e3f"
down_revision: Union[str, None] = "7f6a2b8c9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_fts5_search_index(connection: Connection) -> bool:
    """Whether a real FTS5 search_index virtual table exists on this connection.

    Trigger: SQLite creates search_index as an FTS5 virtual table at runtime
    via SearchRepository.init_search_index, not through Alembic, so fresh
    installs hit this migration before the table exists. Some migration
    tests also stand up a minimal plain `search_index` table to exercise an
    earlier migration's repair SQL in isolation.
    Why: recreating a table Alembic never created would make a fresh install
    diverge from every other install; recreating a same-named table that
    isn't actually the FTS5 index would destroy unrelated data for no schema
    benefit.
    Outcome: only a genuine FTS5 search_index gets dropped and rebuilt with
    search_tokens. A missing table, or a differently-shaped one, is left
    alone -- the runtime creates the real one with the current schema on
    first use.
    """
    row = connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'search_index'")
    ).fetchone()
    return row is not None and row[0] is not None and "fts5" in row[0].lower()


def upgrade() -> None:
    """Add derived CJK search-token storage for SQLite and PostgreSQL.

    Schema only: the Python bigram transform (repository/search_query.py
    cjk_search_tokens, added in the prior commit) is deliberately not
    duplicated in SQL or PL/pgSQL. Existing rows stay stale until the forced
    reindex below runs, consistent with this project's derived-state
    convergence model.
    """
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and _has_fts5_search_index(connection):
        # search_index is a derived FTS5 virtual table with no foreign keys to
        # entity/note_content/observation/relation (FTS5 can't carry one), so
        # dropping and recreating it cannot touch canonical source data -- it
        # only empties the derived index, which the reindex below repopulates.
        op.execute("DROP TABLE IF EXISTS search_index")
        op.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                -- Core entity fields
                id UNINDEXED,          -- Row ID
                title,                 -- Title for searching
                content_stems,         -- Main searchable content split into stems
                content_snippet,       -- File content snippet for display
                search_tokens,         -- Derived CJK bigram tokens (cjk_search_tokens)
                permalink,             -- Stable identifier (now indexed for path search)
                file_path UNINDEXED,   -- Physical location
                type UNINDEXED,        -- entity/relation/observation

                -- Project context
                project_id UNINDEXED,  -- Project identifier

                -- Relation fields
                from_id UNINDEXED,     -- Source entity
                to_id UNINDEXED,       -- Target entity
                relation_type UNINDEXED, -- Type of relation

                -- Observation fields
                entity_id UNINDEXED,   -- Parent entity
                category UNINDEXED,    -- Observation category

                -- Common fields
                metadata UNINDEXED,    -- JSON metadata
                created_at UNINDEXED,  -- Creation timestamp
                updated_at UNINDEXED,  -- Last update

                -- Configuration
                tokenize='unicode61 tokenchars 0x2F',  -- Hex code for /
                prefix='1,2,3,4'                    -- Support longer prefixes for paths
            );
        """)
    elif connection.dialect.name == "postgresql":
        op.execute("ALTER TABLE search_index ADD COLUMN IF NOT EXISTS search_tokens TEXT")
        op.execute("""
            ALTER TABLE search_index ADD COLUMN IF NOT EXISTS search_tokens_index_col tsvector
            GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(search_tokens, ''))
            ) STORED
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_index_cjk_fts
            ON search_index USING gin(search_tokens_index_col)
        """)

        op.execute("ALTER TABLE search_index_fts_chunks ADD COLUMN IF NOT EXISTS chunk_tokens TEXT")
        op.execute("""
            ALTER TABLE search_index_fts_chunks
            ADD COLUMN IF NOT EXISTS chunk_tokens_index_col tsvector
            GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(chunk_tokens, ''))
            ) STORED
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_search_index_fts_chunks_cjk_fts
            ON search_index_fts_chunks USING gin(chunk_tokens_index_col)
        """)

    print("\nCJK search index added. Run: basic-memory reindex --full --search\n")


def downgrade() -> None:
    """Remove the CJK search-token storage, restoring the prior schema exactly."""
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and _has_fts5_search_index(connection):
        op.execute("DROP TABLE IF EXISTS search_index")
        op.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                -- Core entity fields
                id UNINDEXED,          -- Row ID
                title,                 -- Title for searching
                content_stems,         -- Main searchable content split into stems
                content_snippet,       -- File content snippet for display
                permalink,             -- Stable identifier (now indexed for path search)
                file_path UNINDEXED,   -- Physical location
                type UNINDEXED,        -- entity/relation/observation

                -- Project context
                project_id UNINDEXED,  -- Project identifier

                -- Relation fields
                from_id UNINDEXED,     -- Source entity
                to_id UNINDEXED,       -- Target entity
                relation_type UNINDEXED, -- Type of relation

                -- Observation fields
                entity_id UNINDEXED,   -- Parent entity
                category UNINDEXED,    -- Observation category

                -- Common fields
                metadata UNINDEXED,    -- JSON metadata
                created_at UNINDEXED,  -- Creation timestamp
                updated_at UNINDEXED,  -- Last update

                -- Configuration
                tokenize='unicode61 tokenchars 0x2F',  -- Hex code for /
                prefix='1,2,3,4'                    -- Support longer prefixes for paths
            );
        """)
    elif connection.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_search_index_fts_chunks_cjk_fts")
        op.execute("""
            ALTER TABLE search_index_fts_chunks
            DROP COLUMN IF EXISTS chunk_tokens_index_col
        """)
        op.execute("ALTER TABLE search_index_fts_chunks DROP COLUMN IF EXISTS chunk_tokens")

        op.execute("DROP INDEX IF EXISTS idx_search_index_cjk_fts")
        op.execute("""
            ALTER TABLE search_index
            DROP COLUMN IF EXISTS search_tokens_index_col
        """)
        op.execute("ALTER TABLE search_index DROP COLUMN IF EXISTS search_tokens")
