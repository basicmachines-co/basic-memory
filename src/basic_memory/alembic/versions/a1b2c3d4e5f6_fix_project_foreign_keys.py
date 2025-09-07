"""fix project foreign keys

Revision ID: a1b2c3d4e5f6
Revises: 647e7a75e2cd
Create Date: 2025-08-19 22:06:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "647e7a75e2cd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Re-establish foreign key constraints that were lost during project table recreation.

    The migration 647e7a75e2cd recreated the project table but did not re-establish
    the foreign key constraint from entity.project_id to project.id, causing
    foreign key constraint failures when trying to delete projects with related entities.
    """
    # SQLite doesn't allow adding foreign key constraints to existing tables easily
    # We need to be careful and handle the case where the constraint might already exist

    with op.batch_alter_table("entity", schema=None) as batch_op:
        # Try to drop existing foreign key constraint (may not exist)
        try:
            batch_op.drop_constraint("fk_entity_project_id", type_="foreignkey")
        except Exception:
            # Constraint may not exist, which is fine - we'll create it next
            pass

        # Add the foreign key constraint with CASCADE DELETE
        # This ensures that when a project is deleted, all related entities are also deleted
        batch_op.create_foreign_key(
            "fk_entity_project_id", "project", ["project_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    """Remove the foreign key constraint."""
    with op.batch_alter_table("entity", schema=None) as batch_op:
        batch_op.drop_constraint("fk_entity_project_id", type_="foreignkey")
