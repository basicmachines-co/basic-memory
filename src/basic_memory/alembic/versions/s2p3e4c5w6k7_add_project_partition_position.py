"""Add the strict accepted-change partition head to projects.

Revision ID: s2p3e4c5w6k7
Revises: bcdbd5a942ca
Create Date: 2026-08-29 20:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s2p3e4c5w6k7"
down_revision: Union[str, None] = "bcdbd5a942ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the project partition head and its durable accepted evidence."""
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "partition_position",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )

    op.create_table(
        "accepted_project_note_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("project_external_id", sa.String(), nullable=False),
        sa.Column("partition_position", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("note_external_id", sa.String(), nullable=False),
        sa.Column("permalink", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("previous_file_path", sa.Text(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("db_version", sa.Integer(), nullable=True),
        sa.Column("db_checksum", sa.String(), nullable=True),
        sa.Column("actor_user_profile_id", sa.String(), nullable=True),
        sa.Column("actor_kind", sa.String(), nullable=True),
        sa.Column("actor_name", sa.String(), nullable=True),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "partition_position",
            name="uq_accepted_project_note_change_project_position",
        ),
    )
    op.create_index(
        "ix_accepted_project_note_change_project_materialized",
        "accepted_project_note_change",
        ["project_id", "materialized_at"],
        unique=False,
    )
    op.create_index(
        "ix_accepted_project_note_change_note_external_id",
        "accepted_project_note_change",
        ["note_external_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove accepted evidence and the project partition head."""
    op.drop_index(
        "ix_accepted_project_note_change_note_external_id",
        table_name="accepted_project_note_change",
    )
    op.drop_index(
        "ix_accepted_project_note_change_project_materialized",
        table_name="accepted_project_note_change",
    )
    op.drop_table("accepted_project_note_change")
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("partition_position")
