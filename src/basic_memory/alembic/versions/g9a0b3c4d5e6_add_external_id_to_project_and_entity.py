"""Add external_id UUID column to project and entity tables

Revision ID: g9a0b3c4d5e6
Revises: f8a9b2c3d4e5
Create Date: 2025-12-29 10:00:00.000000

"""

import uuid
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
    else:
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
    else:
        # SQLite
        result = connection.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None


# revision identifiers, used by Alembic.
revision: str = "g9a0b3c4d5e6"
down_revision: Union[str, None] = "f8a9b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add external_id UUID column to project and entity tables.

    This migration:
    1. Adds external_id column to project table
    2. Adds external_id column to entity table
    3. Generates UUIDs for existing rows
    4. Creates unique indexes on both columns
    """
    connection = op.get_bind()
    dialect = connection.dialect.name

    # -------------------------------------------------------------------------
    # Add external_id to project table
    # -------------------------------------------------------------------------

    if not column_exists(connection, "project", "external_id"):
        # Step 1: Add external_id column as nullable first
        op.add_column("project", sa.Column("external_id", sa.String(), nullable=True))

        # Step 2: Generate UUIDs for existing rows
        if dialect == "postgresql":
            # Postgres has gen_random_uuid() function
            op.execute("""
                UPDATE project
                SET external_id = gen_random_uuid()::text
                WHERE external_id IS NULL
            """)
        else:
            # SQLite: need to generate UUIDs in Python
            result = connection.execute(text("SELECT id FROM project WHERE external_id IS NULL"))
            for row in result:
                new_uuid = str(uuid.uuid4())
                connection.execute(
                    text("UPDATE project SET external_id = :uuid WHERE id = :id"),
                    {"uuid": new_uuid, "id": row[0]},
                )

        # Step 3: Make external_id NOT NULL
        if dialect == "postgresql":
            op.alter_column("project", "external_id", nullable=False)
        else:
            # SQLite requires batch operations for ALTER COLUMN
            with op.batch_alter_table("project") as batch_op:
                batch_op.alter_column("external_id", nullable=False)

    # Step 4: Create unique index on project.external_id (idempotent)
    if not index_exists(connection, "ix_project_external_id"):
        op.create_index("ix_project_external_id", "project", ["external_id"], unique=True)

    # -------------------------------------------------------------------------
    # Add external_id to entity table
    # -------------------------------------------------------------------------

    if not column_exists(connection, "entity", "external_id"):
        # Step 1: Add external_id column as nullable first
        op.add_column("entity", sa.Column("external_id", sa.String(), nullable=True))

        # Step 2: Generate UUIDs for existing rows
        if dialect == "postgresql":
            # Postgres has gen_random_uuid() function
            op.execute("""
                UPDATE entity
                SET external_id = gen_random_uuid()::text
                WHERE external_id IS NULL
            """)
        else:
            # SQLite: need to generate UUIDs in Python
            result = connection.execute(text("SELECT id FROM entity WHERE external_id IS NULL"))
            for row in result:
                new_uuid = str(uuid.uuid4())
                connection.execute(
                    text("UPDATE entity SET external_id = :uuid WHERE id = :id"),
                    {"uuid": new_uuid, "id": row[0]},
                )

        # Step 3: Make external_id NOT NULL
        if dialect == "postgresql":
            op.alter_column("entity", "external_id", nullable=False)
        else:
            # SQLite requires batch operations for ALTER COLUMN
            with op.batch_alter_table("entity") as batch_op:
                batch_op.alter_column("external_id", nullable=False)

    # Step 4: Create unique index on entity.external_id (idempotent)
    if not index_exists(connection, "ix_entity_external_id"):
        op.create_index("ix_entity_external_id", "entity", ["external_id"], unique=True)


def downgrade() -> None:
    """Remove external_id columns from project and entity tables."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    # Drop from entity table
    if index_exists(connection, "ix_entity_external_id"):
        op.drop_index("ix_entity_external_id", table_name="entity")

    if column_exists(connection, "entity", "external_id"):
        if dialect == "postgresql":
            op.drop_column("entity", "external_id")
        else:
            with op.batch_alter_table("entity") as batch_op:
                batch_op.drop_column("external_id")

    # Drop from project table
    if index_exists(connection, "ix_project_external_id"):
        op.drop_index("ix_project_external_id", table_name="project")

    if column_exists(connection, "project", "external_id"):
        if dialect == "postgresql":
            op.drop_column("project", "external_id")
        else:
            with op.batch_alter_table("project") as batch_op:
                batch_op.drop_column("external_id")
