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


def _replace_search_vector(*, include_full_content: bool) -> None:
    """Replace the generated search vector and rebuild its GIN index."""
    content_expression = """
                    coalesce(title, '') || ' ' ||
                    coalesce(content_stems, '')
    """
    if include_full_content:
        content_expression += " || ' ' ||\n                    coalesce(content_snippet, '')"

    op.execute("DROP INDEX IF EXISTS idx_search_index_fts")
    op.execute("ALTER TABLE search_index DROP COLUMN IF EXISTS textsearchable_index_col")
    op.execute(
        f"""
        ALTER TABLE search_index
        ADD COLUMN textsearchable_index_col tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', {content_expression})
        ) STORED
        """
    )
    op.execute(
        "CREATE INDEX idx_search_index_fts ON search_index USING gin(textsearchable_index_col)"
    )


def upgrade() -> None:
    """Include the complete stored note body in PostgreSQL full-text search."""
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        _replace_search_vector(include_full_content=True)


def downgrade() -> None:
    """Restore the legacy title and capped-stems PostgreSQL search vector."""
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        _replace_search_vector(include_full_content=False)
