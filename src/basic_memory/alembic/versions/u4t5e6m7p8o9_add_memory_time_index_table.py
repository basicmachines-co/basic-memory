"""Add memory_time_index table

Revision ID: u4t5e6m7p8o9
Revises: t3n4o5t6e7s8
Create Date: 2026-08-31 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "u4t5e6m7p8o9"
down_revision: Union[str, None] = "t3n4o5t6e7s8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create memory_time_index: the projection of authored valid time (SPEC-82).

    Rows derive from temporal qualifiers written in canonical markdown
    (``@effective[2026-06-10,2026-07-27)``). Like observations and sections they are
    rebuilt under the note_content generation fence on every (re)index and removed with
    the entity, so the table is always reproducible from the notes.

    Every column is a portable scalar, and every type used here renders on both SQLite
    and PostgreSQL, so this migration needs no dialect branching. Bounds are canonical
    fixed-width text rather than DATE/TIMESTAMP: a date bound must never acquire a time
    of day or a timezone (SQLAlchemy's SQLite DateTime silently drops an offset and
    stores the wrong instant), and fixed-width canonical text makes byte-lexicographic
    order chronological, so one identical predicate serves both backends. Native
    PostgreSQL range columns remain a later addition generated from these columns.

    ``source_id`` carries no foreign key by design: it addresses whichever table
    ``source_type`` names (``observation`` today). Referential lifecycle rides on
    ``entity_id``'s cascade plus the fenced replace instead.

    Only ``ix_memory_time_index_lookup`` indexes the filter columns. The bound values
    are deliberately unindexed, and this table is *not* always driven by a full-text
    candidate set: a valid-time filter counts as criteria on its own
    (``SearchQuery.no_criteria``), so a temporal-only search scans the bound columns
    for every row matching project + kind + axis. That is an acceptable scan at
    expected sizes -- one row per authored qualifier, so thousands, not millions. If
    temporal-only queries ever become a hot path, the answer is a native PostgreSQL
    range column with a GiST index, not a btree over these text bounds.
    """
    op.create_table(
        "memory_time_index",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("time_kind", sa.String(length=32), nullable=False),
        sa.Column("range_axis", sa.String(length=16), nullable=False),
        sa.Column("lower_value", sa.String(length=32), nullable=True),
        sa.Column("upper_value", sa.String(length=32), nullable=True),
        sa.Column("lower_inclusive", sa.Boolean(), nullable=False),
        sa.Column("upper_inclusive", sa.Boolean(), nullable=False),
        sa.Column("is_empty", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extractor", sa.String(length=32), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("assertion_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "range_axis IN ('date', 'instant')",
            name="ck_memory_time_index_range_axis",
        ),
        sa.CheckConstraint(
            "NOT is_empty OR (lower_value IS NULL AND upper_value IS NULL)",
            name="ck_memory_time_index_empty_has_no_bounds",
        ),
        sa.CheckConstraint(
            "(lower_value IS NOT NULL OR NOT lower_inclusive) "
            "AND (upper_value IS NOT NULL OR NOT upper_inclusive)",
            name="ck_memory_time_index_unbounded_is_exclusive",
        ),
    )
    op.create_index(
        "ix_memory_time_index_lookup",
        "memory_time_index",
        ["project_id", "time_kind", "range_axis", "source_type", "source_id"],
        unique=False,
    )
    op.create_index(
        "ix_memory_time_index_entity_id",
        "memory_time_index",
        ["entity_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop memory_time_index and its supporting indexes."""
    op.drop_index("ix_memory_time_index_entity_id", table_name="memory_time_index")
    op.drop_index("ix_memory_time_index_lookup", table_name="memory_time_index")
    op.drop_table("memory_time_index")
