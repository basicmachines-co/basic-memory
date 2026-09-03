"""Record when a project's index pass last completed.

Everything else a readiness check can ask about a project is a count, and a
count of zero is ambiguous in the one place it matters: a project with no
pending work and a project that was never indexed both report zero. That is how
`bm status` came to report a freshly added project ready while 25 unindexed
notes sat on disk (#1414). This column carries the missing bit -- NULL means no
pass has ever completed -- so "nothing to do" and "nothing was ever started"
stop looking alike.

Existing projects are backfilled from `updated_at` when they already hold
entities: those demonstrably have an index, and leaving them NULL would newly
describe every upgraded project as never indexed. `updated_at` is a lower bound
on when indexing happened, which is all this column needs to be -- only
NULL-versus-not is load bearing.

Revision ID: w6r7e8a9d0y1
Revises: v5o6b7s8d9e0
Create Date: 2026-09-02 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "w6r7e8a9d0y1"
down_revision: Union[str, None] = "v5o6b7s8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add project.last_indexed_at and backfill projects that already have entities."""
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        sa.text(
            "UPDATE project SET last_indexed_at = updated_at "
            "WHERE EXISTS (SELECT 1 FROM entity WHERE entity.project_id = project.id)"
        )
    )


def downgrade() -> None:
    """Drop the index-completion marker."""
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("last_indexed_at")
