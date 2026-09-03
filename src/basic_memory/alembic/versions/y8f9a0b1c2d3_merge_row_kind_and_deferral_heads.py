"""Merge the two migration heads left by #1440 and #1444.

Both branches revised `v5o6b7s8d9e0`: #1444 added `w6k7i8n9d0a1` (search_index
uniqueness keyed on the row kind) and #1440 added `w6r7e8a9d0y1` then
`x7d8e9f0a1b2` (project.last_indexed_at, entity.vector_sync_deferred_at). Each
PR was green on its own, and the test suite builds schemas with `create_all` and
stamps them, so nothing ran `upgrade head` against the merged graph. With two
heads, Alembic refuses `upgrade head` and every fresh database fails to
initialize.

A merge revision, rather than re-parenting one branch under the other, is what
keeps databases already stamped at either head upgradeable: from `w6k7i8n9d0a1`
the `w6r7…`/`x7d8…` pair still applies, and from `x7d8e9f0a1b2` the row-kind
index still applies. The schema itself needs no change here.

Revision ID: y8f9a0b1c2d3
Revises: w6k7i8n9d0a1, x7d8e9f0a1b2
Create Date: 2026-09-03 10:45:00.000000

"""

from typing import Sequence, Union


revision: str = "y8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = ("w6k7i8n9d0a1", "x7d8e9f0a1b2")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Both parents already applied their schema changes; this only joins them."""


def downgrade() -> None:
    """Splitting back into two heads needs no schema change either."""
