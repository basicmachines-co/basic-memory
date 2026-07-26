"""Add note_file_vacate table

Revision ID: o8j9k0l1m2n3
Revises: n7i8j9k0l1m2
Create Date: 2026-07-26 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "o8j9k0l1m2n3"
down_revision: Union[str, None] = "n7i8j9k0l1m2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create note_file_vacate: durable proof a source path was vacated by a move.

    The indexer keys on (project_id, file_path) to tell a move's lingering source object (skip
    re-import) from a legitimate byte-identical copy (index as new) — see
    basic-memory-cloud#1601.
    """
    op.create_table(
        "note_file_vacate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_checksum", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "file_path", name="uix_note_file_vacate_project_file_path"
        ),
    )
    op.create_index(
        "ix_note_file_vacate_project_id", "note_file_vacate", ["project_id"], unique=False
    )


def downgrade() -> None:
    """Drop note_file_vacate and its supporting index."""
    op.drop_index("ix_note_file_vacate_project_id", table_name="note_file_vacate")
    op.drop_table("note_file_vacate")
