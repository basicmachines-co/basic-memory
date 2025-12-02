"""Add project_id to relation/observation and pg_trgm for fuzzy link resolution

Revision ID: f8a9b2c3d4e5
Revises: 314f1ea54dc4
Create Date: 2025-12-01 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f8a9b2c3d4e5"
down_revision: Union[str, None] = "314f1ea54dc4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add project_id to relation and observation tables, plus pg_trgm indexes.

    This migration:
    1. Adds project_id column to relation and observation tables (denormalization)
    2. Backfills project_id from the associated entity
    3. Enables pg_trgm extension for trigram-based fuzzy matching (Postgres only)
    4. Creates GIN indexes on entity title and permalink for fast similarity searches
    5. Creates partial index on unresolved relations for efficient bulk resolution
    """
    connection = op.get_bind()
    dialect = connection.dialect.name

    # -------------------------------------------------------------------------
    # Add project_id to relation table
    # -------------------------------------------------------------------------

    # Step 1: Add project_id column as nullable first
    op.add_column("relation", sa.Column("project_id", sa.Integer(), nullable=True))

    # Step 2: Backfill project_id from entity.project_id via from_id
    if dialect == "postgresql":
        op.execute("""
            UPDATE relation
            SET project_id = entity.project_id
            FROM entity
            WHERE relation.from_id = entity.id
        """)
    else:
        # SQLite syntax
        op.execute("""
            UPDATE relation
            SET project_id = (
                SELECT entity.project_id
                FROM entity
                WHERE entity.id = relation.from_id
            )
        """)

    # Step 3: Make project_id NOT NULL and add foreign key
    op.alter_column("relation", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_relation_project_id",
        "relation",
        "project",
        ["project_id"],
        ["id"],
    )

    # Step 4: Create index on relation.project_id
    op.create_index("ix_relation_project_id", "relation", ["project_id"])

    # -------------------------------------------------------------------------
    # Add project_id to observation table
    # -------------------------------------------------------------------------

    # Step 1: Add project_id column as nullable first
    op.add_column("observation", sa.Column("project_id", sa.Integer(), nullable=True))

    # Step 2: Backfill project_id from entity.project_id via entity_id
    if dialect == "postgresql":
        op.execute("""
            UPDATE observation
            SET project_id = entity.project_id
            FROM entity
            WHERE observation.entity_id = entity.id
        """)
    else:
        # SQLite syntax
        op.execute("""
            UPDATE observation
            SET project_id = (
                SELECT entity.project_id
                FROM entity
                WHERE entity.id = observation.entity_id
            )
        """)

    # Step 3: Make project_id NOT NULL and add foreign key
    op.alter_column("observation", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_observation_project_id",
        "observation",
        "project",
        ["project_id"],
        ["id"],
    )

    # Step 4: Create index on observation.project_id
    op.create_index("ix_observation_project_id", "observation", ["project_id"])

    # Postgres-specific: pg_trgm and GIN indexes
    if dialect == "postgresql":
        # Enable pg_trgm extension for fuzzy string matching
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

        # Create trigram indexes on entity table for fuzzy matching
        # GIN indexes with gin_trgm_ops support similarity searches
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_entity_title_trgm
            ON entity USING gin (title gin_trgm_ops)
        """)

        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_entity_permalink_trgm
            ON entity USING gin (permalink gin_trgm_ops)
        """)

        # Create partial index on unresolved relations for efficient bulk resolution
        # This makes "WHERE to_id IS NULL AND project_id = X" queries very fast
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_relation_unresolved
            ON relation (project_id, to_name)
            WHERE to_id IS NULL
        """)

        # Create index on relation.to_name for join performance in bulk resolution
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_relation_to_name
            ON relation (to_name)
        """)


def downgrade() -> None:
    """Remove project_id from relation/observation and pg_trgm indexes."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "postgresql":
        # Drop Postgres-specific indexes
        op.execute("DROP INDEX IF EXISTS idx_relation_to_name")
        op.execute("DROP INDEX IF EXISTS idx_relation_unresolved")
        op.execute("DROP INDEX IF EXISTS idx_entity_permalink_trgm")
        op.execute("DROP INDEX IF EXISTS idx_entity_title_trgm")
        # Note: We don't drop the pg_trgm extension as other code may depend on it

    # Drop project_id from observation
    op.drop_index("ix_observation_project_id", table_name="observation")
    op.drop_constraint("fk_observation_project_id", "observation", type_="foreignkey")
    op.drop_column("observation", "project_id")

    # Drop project_id from relation
    op.drop_index("ix_relation_project_id", table_name="relation")
    op.drop_constraint("fk_relation_project_id", "relation", type_="foreignkey")
    op.drop_column("relation", "project_id")
