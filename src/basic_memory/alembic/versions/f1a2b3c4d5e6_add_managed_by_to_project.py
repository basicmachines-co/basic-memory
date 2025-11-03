"""Add managed_by field to distinguish config vs user-created projects

Revision ID: f1a2b3c4d5e6
Revises: e7e1f4367280
Create Date: 2025-11-03 21:57:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e7e1f4367280"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add managed_by column with default value 'config'
    # All existing projects are treated as config-managed for backward compatibility
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("managed_by", sa.String(), nullable=False, server_default="config")
        )


def downgrade() -> None:
    # Remove managed_by column
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("managed_by")
