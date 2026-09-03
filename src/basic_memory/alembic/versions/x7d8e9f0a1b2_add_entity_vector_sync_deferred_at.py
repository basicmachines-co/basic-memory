"""Record that a vector-sync pass deferred the rest of an entity's chunks.

An entity producing more chunks than one shard is processed a shard at a time:
`plan_entity_vector_shard` schedules the first `OVERSIZED_ENTITY_VECTOR_SHARD_SIZE`
and reports the entity incomplete. The chunks it did not schedule have no manifest
row at all, so after shard one the entity looks fully embedded to any query over
`search_vector_chunks` -- which let readiness report IDLE, and `bm status --wait`
return, while the note still had no semantic coverage past its first shard (#1440
review).

Nothing else in the schema records the chunks that were never written, and the
expected set can only be recomputed by re-chunking and re-hashing the entity's
content -- far too expensive for the route a waiter polls. So the sync that makes
the call records it here, and stays the only writer.

Revision ID: x7d8e9f0a1b2
Revises: w6r7e8a9d0y1
Create Date: 2026-09-03 02:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "x7d8e9f0a1b2"
down_revision: Union[str, None] = "w6r7e8a9d0y1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# `entity` carries generated columns (`frontmatter_status` and friends, added by
# d7e8f9a0b1c2 as sa.Computed). SQLite's batch mode implements a column change by
# recreating the table, and that recreation emits the generated columns twice --
# "duplicate column name: frontmatter_status". A plain ALTER TABLE avoids the
# recreation entirely; SQLite has supported ADD/DROP COLUMN natively since 3.35,
# which is below the floor for the Python versions this project supports.


def upgrade() -> None:
    """Add entity.vector_sync_deferred_at, NULL meaning no deferred work."""
    op.add_column(
        "entity", sa.Column("vector_sync_deferred_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Drop the deferral marker."""
    op.drop_column("entity", "vector_sync_deferred_at")
