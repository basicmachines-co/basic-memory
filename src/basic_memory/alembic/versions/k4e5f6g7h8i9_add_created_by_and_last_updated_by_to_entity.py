"""Add created_by and last_updated_by columns to entity table.

Revision ID: k4e5f6g7h8i9
Revises: j3d4e5f6g7h8
Create Date: 2026-02-23 00:00:00.000000

These columns track which cloud user created and last modified each entity.
Both are nullable — NULL for local/CLI usage and existing entities.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "k4e5f6g7h8i9"
down_revision: Union[str, None] = "j3d4e5f6g7h8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    else:
        # SQLite
        result = connection.execute(text(f"PRAGMA table_info({table})"))
        columns = [row[1] for row in result]
        return column in columns


def upgrade() -> None:
    """Add created_by and last_updated_by columns to entity table.

    Both columns are nullable strings that store cloud user_profile_id UUIDs.
    No data backfill — existing rows get NULL.
    """
    connection = op.get_bind()

    if not column_exists(connection, "entity", "created_by"):
        op.add_column("entity", sa.Column("created_by", sa.String(), nullable=True))

    if not column_exists(connection, "entity", "last_updated_by"):
        op.add_column("entity", sa.Column("last_updated_by", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove created_by and last_updated_by columns from entity table."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if column_exists(connection, "entity", "last_updated_by"):
        if dialect == "postgresql":
            op.drop_column("entity", "last_updated_by")
        else:
            with op.batch_alter_table("entity") as batch_op:
                batch_op.drop_column("last_updated_by")

    if column_exists(connection, "entity", "created_by"):
        if dialect == "postgresql":
            op.drop_column("entity", "created_by")
        else:
            with op.batch_alter_table("entity") as batch_op:
                batch_op.drop_column("created_by")
