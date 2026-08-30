"""Reconcile accepted project change storage for pre-release tenants.

Revision ID: t3q4r5s6x7y8
Revises: s2p3e4c5w6k7
Create Date: 2026-08-30 02:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "t3q4r5s6x7y8"
down_revision: Union[str, None] = "s2p3e4c5w6k7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_accepted_project_note_change() -> None:
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


def _create_missing_indexes(existing_indexes: set[str]) -> None:
    indexes = {
        "ix_accepted_project_note_change_project_materialized": [
            "project_id",
            "materialized_at",
        ],
        "ix_accepted_project_note_change_note_external_id": ["note_external_id"],
    }
    for index_name, columns in indexes.items():
        if index_name not in existing_indexes:
            op.create_index(
                index_name,
                "accepted_project_note_change",
                columns,
                unique=False,
            )


def _reconcile_project_partition_heads() -> None:
    # Some pre-release databases retained journal rows while the project head
    # was absent or reset. The durable journal is authoritative: the next
    # accepted mutation must claim a position strictly above every retained row.
    op.execute(
        sa.text(
            """
            UPDATE project
            SET partition_position = (
                SELECT MAX(accepted_project_note_change.partition_position)
                FROM accepted_project_note_change
                WHERE accepted_project_note_change.project_id = project.id
            )
            WHERE partition_position < (
                SELECT MAX(accepted_project_note_change.partition_position)
                FROM accepted_project_note_change
                WHERE accepted_project_note_change.project_id = project.id
            )
            """
        )
    )


def upgrade() -> None:
    """Repair tenants stamped while the preceding revision was still pre-release."""
    connection = op.get_bind()
    inspector = inspect(connection)

    project_columns = {column["name"] for column in inspector.get_columns("project")}
    if "partition_position" not in project_columns:
        with op.batch_alter_table("project", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "partition_position",
                    sa.Integer(),
                    server_default=sa.text("0"),
                    nullable=False,
                )
            )

    table_names = set(inspector.get_table_names())
    if "accepted_project_note_change" not in table_names:
        _create_accepted_project_note_change()
        _create_missing_indexes(set())
        return

    change_columns = {
        column["name"]
        for column in inspector.get_columns("accepted_project_note_change")
    }
    if "permalink" not in change_columns:
        with op.batch_alter_table(
            "accepted_project_note_change",
            schema=None,
        ) as batch_op:
            batch_op.add_column(sa.Column("permalink", sa.Text(), nullable=True))

        # Pre-release journal rows may outlive their source entity. Preserve the
        # canonical permalink when the entity remains, and use the immutable
        # external id as stable replay identity for deleted legacy rows.
        op.execute(
            sa.text(
                """
                UPDATE accepted_project_note_change
                SET permalink = COALESCE(
                    (
                        SELECT entity.permalink
                        FROM entity
                        WHERE entity.id = accepted_project_note_change.entity_id
                          AND entity.project_id = accepted_project_note_change.project_id
                    ),
                    note_external_id
                )
                """
            )
        )
        with op.batch_alter_table(
            "accepted_project_note_change",
            schema=None,
        ) as batch_op:
            batch_op.alter_column(
                "permalink",
                existing_type=sa.Text(),
                nullable=False,
            )

    existing_indexes = {
        index["name"]
        for index in inspector.get_indexes("accepted_project_note_change")
        if index["name"] is not None
    }
    _create_missing_indexes(existing_indexes)
    _reconcile_project_partition_heads()


def downgrade() -> None:
    """Keep the schema promised by the preceding revision."""
