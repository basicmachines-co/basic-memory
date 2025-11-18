"""Add Postgres full-text search support with tsvector and GIN indexes

Revision ID: 314f1ea54dc4
Revises: e7e1f4367280
Create Date: 2025-11-15 18:05:01.025405

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "314f1ea54dc4"
down_revision: Union[str, None] = "e7e1f4367280"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add PostgreSQL full-text search support.

    This migration:
    1. Creates search_index table for Postgres (SQLite uses FTS5 virtual table)
    2. Adds generated tsvector column for full-text search
    3. Creates GIN index on the tsvector column for fast text queries
    4. Creates GIN index on metadata JSONB column for fast containment queries

    Note: These changes only apply to Postgres. SQLite continues to use FTS5 virtual tables.
    """
    # Check if we're using Postgres
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Create search_index table for Postgres
        # For SQLite, this is a FTS5 virtual table created elsewhere
        from sqlalchemy.dialects.postgresql import JSONB

        op.create_table(
            "search_index",
            sa.Column("id", sa.Integer(), nullable=False),  # Entity IDs are integers
            sa.Column("project_id", sa.Integer(), nullable=False),  # Multi-tenant isolation
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column("content_stems", sa.Text(), nullable=True),
            sa.Column("content_snippet", sa.Text(), nullable=True),
            sa.Column("permalink", sa.String(), nullable=True),  # Nullable for non-markdown files
            sa.Column("file_path", sa.String(), nullable=True),
            sa.Column("type", sa.String(), nullable=True),
            sa.Column("from_id", sa.Integer(), nullable=True),  # Relation IDs are integers
            sa.Column("to_id", sa.Integer(), nullable=True),  # Relation IDs are integers
            sa.Column("relation_type", sa.String(), nullable=True),
            sa.Column("entity_id", sa.Integer(), nullable=True),  # Entity IDs are integers
            sa.Column("category", sa.String(), nullable=True),
            sa.Column("metadata", JSONB(), nullable=True),  # Use JSONB for Postgres
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint(
                "id", "type", "project_id"
            ),  # Composite key: id can repeat across types
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["project.id"],
                name="fk_search_index_project_id",
                ondelete="CASCADE",
            ),
            if_not_exists=True,
        )

        # Create index on project_id for efficient multi-tenant queries
        op.create_index(
            "ix_search_index_project_id",
            "search_index",
            ["project_id"],
            unique=False,
        )

        # Create unique partial index on permalink for markdown files
        # Non-markdown files don't have permalinks, so we use a partial index
        op.execute("""
            CREATE UNIQUE INDEX uix_search_index_permalink_project
            ON search_index (permalink, project_id)
            WHERE permalink IS NOT NULL
        """)

        # Add tsvector column as a GENERATED ALWAYS column
        # This automatically updates when title or content_stems change
        op.execute("""
            ALTER TABLE search_index
            ADD COLUMN textsearchable_index_col tsvector
            GENERATED ALWAYS AS (
                to_tsvector('english',
                    coalesce(title, '') || ' ' ||
                    coalesce(content_stems, '')
                )
            ) STORED
        """)

        # Create GIN index on tsvector column for fast full-text search
        op.create_index(
            "idx_search_index_fts",
            "search_index",
            ["textsearchable_index_col"],
            unique=False,
            postgresql_using="gin",
        )

        # Create GIN index on metadata JSONB for fast containment queries
        # Using jsonb_path_ops for smaller index size and better performance
        op.execute("""
            CREATE INDEX idx_search_index_metadata_gin
            ON search_index
            USING GIN (metadata jsonb_path_ops)
        """)


def downgrade() -> None:
    """Remove PostgreSQL full-text search support."""
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Drop indexes first
        op.execute("DROP INDEX IF EXISTS idx_search_index_metadata_gin")
        op.drop_index("idx_search_index_fts", table_name="search_index")
        op.execute("DROP INDEX IF EXISTS uix_search_index_permalink_project")
        op.drop_index("ix_search_index_project_id", table_name="search_index")

        # Drop the generated column
        op.execute("ALTER TABLE search_index DROP COLUMN IF EXISTS textsearchable_index_col")

        # Drop the search_index table
        op.drop_table("search_index")
