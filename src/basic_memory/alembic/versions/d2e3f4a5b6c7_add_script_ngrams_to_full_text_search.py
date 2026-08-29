"""Add portable script n-grams to full-text search.

Revision ID: d2e3f4a5b6c7
Revises: bcdbd5a942ca
Create Date: 2026-08-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "bcdbd5a942ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SQLITE_COLUMNS = """
    id, title, content_stems, content_snippet, {script_column} permalink,
    file_path, type, project_id, from_id, to_id, relation_type, entity_id,
    category, metadata, created_at, updated_at
"""


def rebuild_sqlite_search_index(*, include_script_ngrams: bool) -> None:
    """Copy the FTS5 table while changing its indexed-column contract."""
    search_index_exists = inspect(op.get_bind()).has_table("search_index")
    script_definition = "script_ngrams," if include_script_ngrams else ""
    op.execute(f"""
        CREATE VIRTUAL TABLE search_index_rebuilt USING fts5(
            id UNINDEXED,
            title,
            content_stems,
            content_snippet,
            {script_definition}
            permalink,
            file_path UNINDEXED,
            type UNINDEXED,
            project_id UNINDEXED,
            from_id UNINDEXED,
            to_id UNINDEXED,
            relation_type UNINDEXED,
            entity_id UNINDEXED,
            category UNINDEXED,
            metadata UNINDEXED,
            created_at UNINDEXED,
            updated_at UNINDEXED,
            tokenize='unicode61 tokenchars 0x2F',
            prefix='1,2,3,4'
        )
    """)

    if not search_index_exists:
        op.execute("ALTER TABLE search_index_rebuilt RENAME TO search_index")
        return

    source_columns = SQLITE_COLUMNS.format(script_column="")
    target_columns = SQLITE_COLUMNS.format(
        script_column="script_ngrams," if include_script_ngrams else ""
    )
    selected_columns = SQLITE_COLUMNS.format(script_column="'' AS script_ngrams,")
    if not include_script_ngrams:
        selected_columns = source_columns
    op.execute(
        f"INSERT INTO search_index_rebuilt ({target_columns}) "
        f"SELECT {selected_columns} FROM search_index"
    )
    op.execute("DROP TABLE search_index")
    op.execute("ALTER TABLE search_index_rebuilt RENAME TO search_index")


def upgrade() -> None:
    """Add the derived script channel; a reindex populates existing rows."""
    if op.get_bind().dialect.name == "sqlite":
        rebuild_sqlite_search_index(include_script_ngrams=True)
        return

    op.execute("ALTER TABLE search_index ADD COLUMN script_ngrams TEXT NOT NULL DEFAULT ''")
    op.execute("""
        ALTER TABLE search_index
        ADD COLUMN script_ngrams_index_col tsvector GENERATED ALWAYS AS (
            to_tsvector('simple', script_ngrams)
        ) STORED
    """)
    op.execute("""
        CREATE INDEX idx_search_index_script_ngrams_fts
        ON search_index USING gin(script_ngrams_index_col)
    """)
    op.execute(
        "ALTER TABLE search_index_fts_chunks ADD COLUMN script_ngrams TEXT NOT NULL DEFAULT ''"
    )
    op.execute("""
        ALTER TABLE search_index_fts_chunks
        ADD COLUMN script_ngrams_index_col tsvector GENERATED ALWAYS AS (
            to_tsvector('simple', script_ngrams)
        ) STORED
    """)
    op.execute("""
        CREATE INDEX idx_search_index_fts_chunks_script_ngrams_fts
        ON search_index_fts_chunks USING gin(script_ngrams_index_col)
    """)


def downgrade() -> None:
    """Remove the script channel while preserving the existing word index."""
    if op.get_bind().dialect.name == "sqlite":
        rebuild_sqlite_search_index(include_script_ngrams=False)
        return

    op.execute("DROP INDEX IF EXISTS idx_search_index_fts_chunks_script_ngrams_fts")
    op.execute("ALTER TABLE search_index_fts_chunks DROP COLUMN IF EXISTS script_ngrams_index_col")
    op.execute("ALTER TABLE search_index_fts_chunks DROP COLUMN IF EXISTS script_ngrams")
    op.execute("DROP INDEX IF EXISTS idx_search_index_script_ngrams_fts")
    op.execute("ALTER TABLE search_index DROP COLUMN IF EXISTS script_ngrams_index_col")
    op.execute("ALTER TABLE search_index DROP COLUMN IF EXISTS script_ngrams")
