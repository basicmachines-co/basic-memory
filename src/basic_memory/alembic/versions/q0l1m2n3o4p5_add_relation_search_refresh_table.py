"""Add durable relation search refresh work.

Revision ID: q0l1m2n3o4p5
Revises: p9k0l1m2n3o4
Create Date: 2026-07-27 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "q0l1m2n3o4p5"
down_revision: Union[str, None] = "p9k0l1m2n3o4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create durable work items for relation-derived search refreshes."""
    op.create_table(
        "relation_search_refresh",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_relation_search_refresh_project_id",
        "relation_search_refresh",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_relation_search_refresh_entity_id",
        "relation_search_refresh",
        ["entity_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop durable relation-search refresh work."""
    op.drop_index(
        "ix_relation_search_refresh_entity_id",
        table_name="relation_search_refresh",
    )
    op.drop_index(
        "ix_relation_search_refresh_project_id",
        table_name="relation_search_refresh",
    )
    op.drop_table("relation_search_refresh")
