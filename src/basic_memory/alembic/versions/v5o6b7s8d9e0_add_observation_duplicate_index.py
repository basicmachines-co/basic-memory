"""Add the duplicate ordinal that keeps same-identity observations addressable.

An observation's synthetic permalink is built from its category and content, both of
which survive the peel that strips a temporal qualifier and a (context) off the authored
line. Two observations differing only in one of those therefore shared one address, and
the permalink-keyed search index kept only the first -- so the second note's authored
valid time addressed an observation with no search row, and queries for its interval
found nothing (SPEC-82).

This ordinal separates such twins. It defaults to 0, which is the value every existing
row takes and the value the first observation of any identity keeps, so no permalink that
resolves today changes. Search rows are derived state and are rebuilt from markdown, so
the second twin becomes addressable on the next index pass for that note.

Revision ID: v5o6b7s8d9e0
Revises: u4t5e6m7p8o9
Create Date: 2026-09-02 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "v5o6b7s8d9e0"
down_revision: Union[str, None] = "u4t5e6m7p8o9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add observation.duplicate_index, defaulting every existing row to 0."""
    with op.batch_alter_table("observation", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "duplicate_index",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )


def downgrade() -> None:
    """Remove the duplicate ordinal."""
    with op.batch_alter_table("observation", schema=None) as batch_op:
        batch_op.drop_column("duplicate_index")
