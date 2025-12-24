"""Add cascade delete FK from search_index to entity

Revision ID: a2b3c4d5e6f7
Revises: f8a9b2c3d4e5
Create Date: 2025-12-02 07:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f8a9b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add FK with CASCADE delete from search_index.entity_id to entity.id.

    This migration is Postgres-only because:
    - SQLite uses FTS5 virtual tables which don't support foreign keys
    - The FK enables automatic cleanup of search_index entries when entities are deleted
    """
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "postgresql":
        # First, clean up any orphaned search_index entries where entity no longer exists
        op.execute("""
            DELETE FROM search_index
            WHERE entity_id IS NOT NULL
            AND entity_id NOT IN (SELECT id FROM entity)
        """)

        # Add FK with CASCADE - nullable FK allows search_index entries without entity_id
        op.create_foreign_key(
            "fk_search_index_entity_id",
            "search_index",
            "entity",
            ["entity_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Remove the FK constraint."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "postgresql":
        op.drop_constraint("fk_search_index_entity_id", "search_index", type_="foreignkey")
