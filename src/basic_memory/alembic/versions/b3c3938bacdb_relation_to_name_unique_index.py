"""relation to_name unique index

Revision ID: b3c3938bacdb
Revises: 3dae7c7b1564
Create Date: 2025-02-22 14:59:30.668466

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b3c3938bacdb"
down_revision: Union[str, None] = "3dae7c7b1564"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite doesn't support constraint changes through ALTER
    # Need to recreate table with desired constraints
    with op.batch_alter_table("relation") as batch_op:
        # Drop existing unique constraint
        batch_op.drop_constraint("uix_relation", type_="unique")

        # Add new constraints
        batch_op.create_unique_constraint(
            "uix_relation_from_id_to_id", ["from_id", "to_id", "relation_type"]
        )
        batch_op.create_unique_constraint(
            "uix_relation_from_id_to_name", ["from_id", "to_name", "relation_type"]
        )


def downgrade() -> None:
    with op.batch_alter_table("relation") as batch_op:
        # Drop new constraints
        batch_op.drop_constraint("uix_relation_from_id_to_name", type_="unique")
        batch_op.drop_constraint("uix_relation_from_id_to_id", type_="unique")

        # Restore original constraint
        batch_op.create_unique_constraint("uix_relation", ["from_id", "to_id", "relation_type"])
