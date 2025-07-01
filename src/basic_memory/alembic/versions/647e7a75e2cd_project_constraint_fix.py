"""project constraint fix

Revision ID: 647e7a75e2cd
Revises: 5fe1ab1ccebe
Create Date: 2025-06-03 12:48:30.162566

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "647e7a75e2cd"
down_revision: Union[str, None] = "5fe1ab1ccebe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Remove the problematic UNIQUE constraint on is_default column.

    The UNIQUE constraint prevents multiple projects from having is_default=FALSE,
    which breaks project creation when the service sets is_default=False.

    Since SQLite doesn't support dropping specific constraints easily, we'll
    recreate the table without the problematic constraint.
    """
    # For SQLite, we need to recreate the table without the UNIQUE constraint
    # Create a new table without the UNIQUE constraint on is_default
    op.create_table(
        "project_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("permalink", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=True),  # No UNIQUE constraint!
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("permalink"),
    )

    # Copy data from old table to new table
    op.execute("INSERT INTO project_new SELECT * FROM project")

    # Drop the old table
    op.drop_table("project")

    # Rename the new table
    op.rename_table("project_new", "project")

    # Recreate the indexes
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.create_index("ix_project_created_at", ["created_at"], unique=False)
        batch_op.create_index("ix_project_name", ["name"], unique=True)
        batch_op.create_index("ix_project_path", ["path"], unique=False)
        batch_op.create_index("ix_project_permalink", ["permalink"], unique=True)
        batch_op.create_index("ix_project_updated_at", ["updated_at"], unique=False)


def downgrade() -> None:
    """Add back the UNIQUE constraint on is_default column.

    WARNING: This will break project creation again if multiple projects
    have is_default=FALSE.
    """
    # Recreate the table with the UNIQUE constraint
    op.create_table(
        "project_old",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("permalink", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("is_default"),  # Add back the problematic constraint
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("permalink"),
    )

    # Copy data (this may fail if multiple FALSE values exist)
    op.execute("INSERT INTO project_old SELECT * FROM project")

    # Drop the current table and rename
    op.drop_table("project")
    op.rename_table("project_old", "project")

    # Recreate indexes
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.create_index("ix_project_created_at", ["created_at"], unique=False)
        batch_op.create_index("ix_project_name", ["name"], unique=True)
        batch_op.create_index("ix_project_path", ["path"], unique=False)
        batch_op.create_index("ix_project_permalink", ["permalink"], unique=True)
        batch_op.create_index("ix_project_updated_at", ["updated_at"], unique=False)
