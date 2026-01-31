"""Add structured metadata indexes for entity frontmatter

Revision ID: d7e8f9a0b1c2
Revises: g9a0b3c4d5e6
Create Date: 2026-01-31 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


def column_exists(connection, table: str, column: str) -> bool:
    """Check if a column exists in a table (idempotent migration support)."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        return result.fetchone() is not None
    # SQLite
    result = connection.execute(text(f"PRAGMA table_info({table})"))
    columns = [row[1] for row in result]
    return column in columns


def index_exists(connection, index_name: str) -> bool:
    """Check if an index exists (idempotent migration support)."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
        {"index_name": index_name},
    )
    return result.fetchone() is not None


# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "6830751f5fb6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add JSONB/GiN indexes for Postgres and generated columns for SQLite."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "postgresql":
        # Ensure JSONB for efficient indexing
        result = connection.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'entity' AND column_name = 'entity_metadata'"
            )
        ).fetchone()
        if result and result[0] != "jsonb":
            op.execute(
                "ALTER TABLE entity ALTER COLUMN entity_metadata "
                "TYPE jsonb USING entity_metadata::jsonb"
            )

        # General JSONB GIN index
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_metadata_gin "
            "ON entity USING GIN (entity_metadata jsonb_path_ops)"
        )

        # Common field indexes
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_tags_json "
            "ON entity USING GIN ((entity_metadata -> 'tags'))"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_frontmatter_type "
            "ON entity ((entity_metadata ->> 'type'))"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_frontmatter_status "
            "ON entity ((entity_metadata ->> 'status'))"
        )
        return

    # SQLite: add generated columns for common frontmatter fields
    if not column_exists(connection, "entity", "tags_json"):
        op.add_column(
            "entity",
            sa.Column(
                "tags_json",
                sa.Text(),
                sa.Computed("json_extract(entity_metadata, '$.tags')", persisted=True),
            ),
        )
    if not column_exists(connection, "entity", "frontmatter_status"):
        op.add_column(
            "entity",
            sa.Column(
                "frontmatter_status",
                sa.Text(),
                sa.Computed("json_extract(entity_metadata, '$.status')", persisted=True),
            ),
        )
    if not column_exists(connection, "entity", "frontmatter_type"):
        op.add_column(
            "entity",
            sa.Column(
                "frontmatter_type",
                sa.Text(),
                sa.Computed("json_extract(entity_metadata, '$.type')", persisted=True),
            ),
        )

    # Index generated columns
    if not index_exists(connection, "idx_entity_tags_json"):
        op.create_index("idx_entity_tags_json", "entity", ["tags_json"])
    if not index_exists(connection, "idx_entity_frontmatter_status"):
        op.create_index("idx_entity_frontmatter_status", "entity", ["frontmatter_status"])
    if not index_exists(connection, "idx_entity_frontmatter_type"):
        op.create_index("idx_entity_frontmatter_type", "entity", ["frontmatter_type"])


def downgrade() -> None:
    """Best-effort downgrade (drop indexes, revert JSONB on Postgres)."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_entity_frontmatter_status")
        op.execute("DROP INDEX IF EXISTS idx_entity_frontmatter_type")
        op.execute("DROP INDEX IF EXISTS idx_entity_tags_json")
        op.execute("DROP INDEX IF EXISTS idx_entity_metadata_gin")
        op.execute(
            "ALTER TABLE entity ALTER COLUMN entity_metadata TYPE json USING entity_metadata::json"
        )
        return

    # SQLite: drop indexes (dropping generated columns requires table rebuild)
    op.execute("DROP INDEX IF EXISTS idx_entity_frontmatter_status")
    op.execute("DROP INDEX IF EXISTS idx_entity_frontmatter_type")
    op.execute("DROP INDEX IF EXISTS idx_entity_tags_json")
