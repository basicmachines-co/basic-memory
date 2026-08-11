"""Dedupe observations and purge stale observation search rows.

Revision ID: 2d26b287813b
Revises: r1m2n3o4p5q6
Create Date: 2026-08-10 19:22:17.344875

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "2d26b287813b"
down_revision: Union[str, None] = "r1m2n3o4p5q6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(connection, table_name: str) -> bool:
    """Return whether a runtime-managed table is present for this database."""
    return table_name in inspect(connection).get_table_names()


def upgrade() -> None:
    """Repair duplicate observation projections and their orphaned search rows."""
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # PostgreSQL JSON has no equality operator, so its textual representation is the
        # grouping key. GROUP BY treats NULL context and tags values consistently.
        op.execute(
            """
            DELETE FROM observation
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM observation
                GROUP BY entity_id, category, content, context, tags::text
            )
            """
        )
    else:
        op.execute(
            """
            DELETE FROM observation
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM observation
                GROUP BY entity_id, category, content, context, tags
            )
            """
        )

    # SQLite creates search_index at runtime rather than through Alembic. A fresh database
    # therefore has no table or stale rows to purge during this migration.
    if _table_exists(connection, "search_index"):
        op.execute(
            """
            DELETE FROM search_index
            WHERE type = 'observation'
              AND id NOT IN (SELECT id FROM observation)
            """
        )


def downgrade() -> None:
    """No-op: removed duplicate and orphaned projection rows cannot be reconstructed."""
    pass
