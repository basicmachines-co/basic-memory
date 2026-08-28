"""relation.to_id foreign key becomes ON DELETE SET NULL

Deleting an entity used to take every inbound relation row with it. The wikilink
stayed in the source note's markdown, but the graph forgot the edge existed, so a
"find broken links" report came back clean over a vault full of them.

to_id has always been nullable, and the indexer already produces exactly the state
we want here -- to_id NULL with to_name holding the source's link text -- for a link
pointing at a note that does not exist yet. A deleted target should land in that same
unresolved state instead of vanishing, and forward-reference resolution then re-links
the row for free if the target is ever recreated.

from_id keeps CASCADE. An entity really does own the relations it declares.

Revision ID: bcdbd5a942ca
Revises: 7f6a2b8c9d10
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "bcdbd5a942ca"
down_revision: Union[str, None] = "7f6a2b8c9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The initial schema created relation's entity foreign keys without names, and SQLite
# has no way to reference an anonymous constraint. Handing batch_alter_table a naming
# convention lets it address the reflected FK by the name the convention would have
# given it. Side effect worth knowing about: the table recreate writes those names into
# the schema, so from_id's FK comes out named too. Cosmetic, and an improvement.
RELATION_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
SQLITE_TO_ID_FK = "fk_relation_to_id_entity"

# Postgres named it for us when the initial schema left it anonymous.
POSTGRES_TO_ID_FK = "relation_to_id_fkey"


def _set_to_id_ondelete(ondelete: str) -> None:
    """Point relation.to_id's foreign key at a different ON DELETE action."""
    dialect = op.get_bind().dialect.name

    if dialect == "sqlite":
        # SQLite cannot alter a foreign key in place; batch mode does the copy-and-swap.
        # Same shape as a1b2c3d4e5f6.
        with op.batch_alter_table(
            "relation", schema=None, naming_convention=RELATION_NAMING_CONVENTION
        ) as batch_op:
            batch_op.drop_constraint(SQLITE_TO_ID_FK, type_="foreignkey")
            batch_op.create_foreign_key(
                SQLITE_TO_ID_FK, "entity", ["to_id"], ["id"], ondelete=ondelete
            )
    else:
        op.drop_constraint(POSTGRES_TO_ID_FK, "relation", type_="foreignkey")
        op.create_foreign_key(
            POSTGRES_TO_ID_FK, "relation", "entity", ["to_id"], ["id"], ondelete=ondelete
        )


def upgrade() -> None:
    """Stop erasing an entity's inbound relations along with the entity."""
    _set_to_id_ondelete("SET NULL")


def downgrade() -> None:
    """Go back to erasing inbound relations on delete.

    Rows already unresolved (to_id NULL) stay that way. This restores the constraint;
    it cannot un-forget what CASCADE already ate.
    """
    _set_to_id_ondelete("CASCADE")
