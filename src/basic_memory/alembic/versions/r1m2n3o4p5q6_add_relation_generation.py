"""Add generation ownership to relation projections.

Revision ID: r1m2n3o4p5q6
Revises: q0l1m2n3o4p5
Create Date: 2026-08-09 17:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "r1m2n3o4p5q6"
down_revision: Union[str, None] = "q0l1m2n3o4p5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill relation rows with their source note's accepted generation."""
    op.add_column(
        "relation",
        sa.Column(
            "generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE relation
            SET generation = note_content.db_version
            FROM note_content
            WHERE relation.from_id = note_content.entity_id
              AND relation.project_id = note_content.project_id
            """
        )
    else:
        op.execute(
            """
            UPDATE relation
            SET generation = COALESCE(
                (
                    SELECT note_content.db_version
                    FROM note_content
                    WHERE note_content.entity_id = relation.from_id
                      AND note_content.project_id = relation.project_id
                ),
                0
            )
            """
        )

    op.create_index(
        "ix_relation_project_from_generation",
        "relation",
        ["project_id", "from_id", "generation"],
        unique=False,
    )


def downgrade() -> None:
    """Remove relation generation ownership."""
    op.drop_index("ix_relation_project_from_generation", table_name="relation")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_column("relation", "generation")
    else:
        with op.batch_alter_table("relation") as batch_op:
            batch_op.drop_column("generation")
