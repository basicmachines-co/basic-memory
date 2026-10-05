"""Record the checksum a file was synced in with, before indexing rewrote it.

Indexing writes frontmatter (a permalink, and a title and type when missing) back into a
Markdown file that arrived without it. A one-way sync client (rclone sync, a backup
script) then sees a remote file that differs from its local copy and copies the original
back. Indexing reads that original as a change, rewrites it again, and the two loop
forever: one customer's workspace re-indexed about 3,800 files every 30 minutes this way
(basic-memory-cloud#2350).

`entity.checksum` holds our rewritten file, so it cannot recognize the original. This
column holds the original, so a re-sync of it is recognized as already indexed. It is
NULL when indexing did not rewrite the file.

Revision ID: z9a0b1c2d3e4
Revises: y8f9a0b1c2d3
Create Date: 2026-10-05 20:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z9a0b1c2d3e4"
down_revision: Union[str, None] = "y8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# A plain ALTER TABLE, not batch mode: `entity` carries generated columns, and SQLite's
# batch recreation emits them twice (see x7d8e9f0a1b2).


def upgrade() -> None:
    """Add entity.sync_checksum, NULL meaning indexing did not rewrite the file."""
    op.add_column("entity", sa.Column("sync_checksum", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the sync checksum."""
    op.drop_column("entity", "sync_checksum")
