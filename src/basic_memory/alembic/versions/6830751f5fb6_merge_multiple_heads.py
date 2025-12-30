"""Merge multiple heads

Revision ID: 6830751f5fb6
Revises: a2b3c4d5e6f7, g9a0b3c4d5e6
Create Date: 2025-12-29 12:46:46.476268

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6830751f5fb6'
down_revision: Union[str, Sequence[str], None] = ('a2b3c4d5e6f7', 'g9a0b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
