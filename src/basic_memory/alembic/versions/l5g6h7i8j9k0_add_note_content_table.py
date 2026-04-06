"""Add note_content table

Revision ID: l5g6h7i8j9k0
Revises: k4e5f6g7h8i9
Create Date: 2026-04-04 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "l5g6h7i8j9k0"
down_revision: Union[str, None] = "k4e5f6g7h8i9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create note_content for materialized note content and sync state."""
    op.create_table(
        "note_content",
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("markdown_content", sa.Text(), nullable=False),
        sa.Column("db_version", sa.BigInteger(), nullable=False),
        sa.Column("db_checksum", sa.String(), nullable=False),
        sa.Column("file_version", sa.BigInteger(), nullable=True),
        sa.Column("file_checksum", sa.String(), nullable=True),
        sa.Column("file_write_status", sa.String(), nullable=False),
        sa.Column("last_source", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_materialization_error", sa.Text(), nullable=True),
        sa.Column("last_materialization_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "file_write_status IN ("
            "'pending', "
            "'writing', "
            "'synced', "
            "'failed', "
            "'external_change_detected'"
            ")",
            name="ck_note_content_file_write_status",
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("entity_id"),
    )
    op.create_index("ix_note_content_project_id", "note_content", ["project_id"], unique=False)
    op.create_index("ix_note_content_file_path", "note_content", ["file_path"], unique=False)
    op.create_index("ix_note_content_external_id", "note_content", ["external_id"], unique=True)


def downgrade() -> None:
    """Drop note_content and its supporting indexes."""
    op.drop_index("ix_note_content_external_id", table_name="note_content")
    op.drop_index("ix_note_content_file_path", table_name="note_content")
    op.drop_index("ix_note_content_project_id", table_name="note_content")
    op.drop_table("note_content")
