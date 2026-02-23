"""Rename entity_type column to note_type

Revision ID: j3d4e5f6g7h8
Revises: i2c3d4e5f6g7
Create Date: 2026-02-22 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "j3d4e5f6g7h8"
down_revision: Union[str, None] = "i2c3d4e5f6g7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(connection, table_name: str) -> bool:
    """Check if a table exists (idempotent migration support)."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :table_name"
            ),
            {"table_name": table_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


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


def upgrade() -> None:
    """Rename entity_type → note_type on the entity table."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    # Skip if already migrated (idempotent)
    if column_exists(connection, "entity", "note_type"):
        return

    if dialect == "postgresql":
        # Postgres supports direct column rename
        op.execute("ALTER TABLE entity RENAME COLUMN entity_type TO note_type")

        # Recreate the index with new name
        op.execute("DROP INDEX IF EXISTS ix_entity_type")
        op.execute("CREATE INDEX ix_note_type ON entity (note_type)")
    else:
        # SQLite 3.25.0+ supports ALTER TABLE RENAME COLUMN directly.
        # Avoids batch_alter_table which fails on tables with generated columns
        # (duplicate column name error when recreating the table).
        op.execute("ALTER TABLE entity RENAME COLUMN entity_type TO note_type")

        # Recreate the index with new name
        if index_exists(connection, "ix_entity_type"):
            op.drop_index("ix_entity_type", table_name="entity")
        op.create_index("ix_note_type", "entity", ["note_type"])

    # Update search index metadata: rename entity_type → note_type in JSON
    # This updates the stored metadata so search results use the new field name
    # Guard: search_index may not exist on a fresh DB (created by an earlier migration)
    if not table_exists(connection, "search_index"):
        return

    if dialect == "postgresql":
        op.execute(
            text("""
                UPDATE search_index
                SET metadata = metadata - 'entity_type' || jsonb_build_object('note_type', metadata->'entity_type')
                WHERE metadata ? 'entity_type'
            """)
        )
    else:
        op.execute(
            text("""
                UPDATE search_index
                SET metadata = json_set(
                    json_remove(metadata, '$.entity_type'),
                    '$.note_type',
                    json_extract(metadata, '$.entity_type')
                )
                WHERE json_extract(metadata, '$.entity_type') IS NOT NULL
            """)
        )


def downgrade() -> None:
    """Rename note_type → entity_type on the entity table."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "postgresql":
        op.execute("ALTER TABLE entity RENAME COLUMN note_type TO entity_type")
        op.execute("DROP INDEX IF EXISTS ix_note_type")
        op.execute("CREATE INDEX ix_entity_type ON entity (entity_type)")
    else:
        op.execute("ALTER TABLE entity RENAME COLUMN note_type TO entity_type")

        if index_exists(connection, "ix_note_type"):
            op.drop_index("ix_note_type", table_name="entity")
        op.create_index("ix_entity_type", "entity", ["entity_type"])

    # Revert search index metadata
    if not table_exists(connection, "search_index"):
        return

    if dialect == "postgresql":
        op.execute(
            text("""
                UPDATE search_index
                SET metadata = metadata - 'note_type' || jsonb_build_object('entity_type', metadata->'note_type')
                WHERE metadata ? 'note_type'
            """)
        )
    else:
        op.execute(
            text("""
                UPDATE search_index
                SET metadata = json_set(
                    json_remove(metadata, '$.note_type'),
                    '$.entity_type',
                    json_extract(metadata, '$.note_type')
                )
                WHERE json_extract(metadata, '$.note_type') IS NOT NULL
            """)
        )
