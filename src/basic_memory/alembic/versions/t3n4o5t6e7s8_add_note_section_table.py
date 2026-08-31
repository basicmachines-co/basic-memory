"""Add note_section table

Revision ID: t3n4o5t6e7s8
Revises: s2p3e4c5w6k7
Create Date: 2026-08-30 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "t3n4o5t6e7s8"
down_revision: Union[str, None] = "s2p3e4c5w6k7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create note_section: a per-generation index of heading-bounded note body spans.

    Rows are coordinates into the canonical markdown (body-relative lines and utf-8 byte
    offsets), never content copies; rebuilt under the note_content generation fence on
    every (re)index and removed with the entity (SPEC-47 / #1403). The lookup index keys
    on a sha256 digest of the heading path so arbitrary heading text cannot exceed
    PostgreSQL's btree index-row limit.
    """
    op.create_table(
        "note_section",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=False),
        sa.Column("heading_path_digest", sa.String(length=64), nullable=False),
        sa.Column("duplicate_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_note_section_project_id", "note_section", ["project_id"], unique=False)
    op.create_index("ix_note_section_entity_id", "note_section", ["entity_id"], unique=False)
    op.create_index(
        "ix_note_section_entity_path",
        "note_section",
        ["entity_id", "heading_path_digest", "duplicate_index"],
        unique=False,
    )


def downgrade() -> None:
    """Drop note_section and its supporting indexes."""
    op.drop_index("ix_note_section_entity_path", table_name="note_section")
    op.drop_index("ix_note_section_entity_id", table_name="note_section")
    op.drop_index("ix_note_section_project_id", table_name="note_section")
    op.drop_table("note_section")
