"""Empty revision kept for migration-chain continuity.

The name promises an embedding backfill, but no migration hook ever ran one (#1826).

Revision ID: i2c3d4e5f6g7
Revises: h1b2c3d4e5f6
Create Date: 2026-02-19 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "i2c3d4e5f6g7"
down_revision: Union[str, None] = "h1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No schema change and no data change.

    An earlier docstring said db.run_migrations() detects this revision and backfills
    embeddings. It never did. Notes without vectors converge through index passes
    instead: the project-index pass embeds what it indexes and restores entities that
    have no search rows, and `bm reindex --embeddings` embeds every eligible note.
    """


def downgrade() -> None:
    """No-op downgrade."""
