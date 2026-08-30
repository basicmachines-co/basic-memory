"""Merge script search and accepted project partition heads.

Revision ID: u4r5s6t7y8z9
Revises: d2e3f4a5b6c7, t3q4r5s6x7y8
Create Date: 2026-08-30 12:00:00.000000

"""

from typing import Sequence, Union


revision: str = "u4r5s6t7y8z9"
down_revision: Union[str, Sequence[str], None] = (
    "d2e3f4a5b6c7",
    "t3q4r5s6x7y8",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join the independently released schema branches."""


def downgrade() -> None:
    """Return to the two independent schema heads."""
