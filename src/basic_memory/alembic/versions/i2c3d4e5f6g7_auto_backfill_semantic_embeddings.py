"""Trigger automatic semantic embedding backfill during migration.

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
    """No schema change.

    Trigger: this revision is newly applied.
    Why: db.run_migrations() detects this revision transition and runs the existing
    sync_entity_vectors() pipeline to backfill semantic embeddings automatically.
    Outcome: users no longer need to run `bm reindex --embeddings` after upgrading.
    """


def downgrade() -> None:
    """No-op downgrade."""
