"""Drop the Postgres btree indexes on frontmatter ``status`` and ``type``.

Revision d7e8f9a0b1c2 indexed ``entity_metadata ->> 'status'`` and
``entity_metadata ->> 'type'`` with plain btrees. Frontmatter values are free text, and a
btree entry cannot exceed 2704 bytes, so a note whose ``status`` runs to a few kilobytes
fails to insert with ``ProgramLimitExceededError``. The failure repeats on every reindex,
and the note never reaches search, the graph, or listings.

Nothing reads these indexes. Metadata filters query
``jsonb_extract_path_text(entity_metadata::jsonb, :path)`` with the path bound as a
parameter, which the planner cannot match to a ``->> 'status'`` expression index.
Containment filters use the GIN index on ``entity_metadata``, which stays.

SQLite is unchanged: its indexes on the generated ``frontmatter_status`` and
``frontmatter_type`` columns have no entry size limit, and its metadata filters read
those columns.

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-05 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the frontmatter btree indexes on Postgres."""
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS idx_entity_frontmatter_status")
    op.execute("DROP INDEX IF EXISTS idx_entity_frontmatter_type")


def downgrade() -> None:
    """Recreate the frontmatter btree indexes on Postgres.

    This fails if any note now carries a ``status`` or ``type`` over the btree entry
    limit, which is the condition the upgrade exists to allow.
    """
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_frontmatter_type "
        "ON entity ((entity_metadata ->> 'type'))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_frontmatter_status "
        "ON entity ((entity_metadata ->> 'status'))"
    )
