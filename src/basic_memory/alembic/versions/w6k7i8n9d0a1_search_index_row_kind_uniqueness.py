"""Key search_index uniqueness on the row kind as well as the permalink.

A relation's permalink is `from/type/to` with the relation type authored by the user, so
a note that says

    - [decision] redis
    - observations [[decision/redis]]

hands its observation and its relation the same address. Keyed on the permalink alone,
Postgres resolved that by upserting one row into the other, which then broke the
FTS-chunk foreign key still pointing at the row kind it had just overwritten; SQLite,
whose search_index is an FTS5 virtual table with no unique index at all, kept both under
one address with nothing to say which was which (#1437). No reserved path segment closes
it, because the colliding segment is the author's own text.

The row kind is already half of this table's primary key, `(id, type, project_id)`. This
widens the unique index to agree with it. Widening cannot fail on existing data -- every
row that satisfied the narrow key satisfies the wide one -- and no permalink changes, so
nothing anyone has linked to moves.

SQLite needs no schema change here: its FTS5 virtual table carries no unique index, and
the rule it does obey lives in the repository's delete-before-insert, which now scopes by
row kind from the same shared key.

Revision ID: w6k7i8n9d0a1
Revises: v5o6b7s8d9e0
Create Date: 2026-09-03 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "w6k7i8n9d0a1"
down_revision: Union[str, None] = "v5o6b7s8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Replace the permalink-only unique index with one that includes the row kind."""
    if op.get_bind().dialect.name != "postgresql":
        return

    # The wide index is created before the narrow one is dropped so the table is never
    # momentarily unconstrained, which matters if this ever runs outside a transaction.
    # The DDL must stay identical to CREATE_POSTGRES_SEARCH_INDEX_PERMALINK in
    # models/search.py, which is what the test suite creates instead of migrating.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uix_search_index_permalink_type_project
        ON search_index (permalink, type, project_id)
        WHERE permalink IS NOT NULL
    """)
    op.execute("DROP INDEX IF EXISTS uix_search_index_permalink_project")


def downgrade() -> None:
    """Restore the permalink-only unique index, dropping the rows it cannot admit."""
    if op.get_bind().dialect.name != "postgresql":
        return

    # Trigger: the narrow index cannot be recreated while one permalink is held by two
    # row kinds -- exactly what the upgrade made legal.
    # Why: search_index is derived state, rebuilt from markdown by the next index pass,
    # so shedding the extra projections is recoverable where a failed downgrade is not.
    # Outcome: one row survives per (project_id, permalink), preferring entity over
    # observation over relation because `type` sorts that way, and the FTS chunk rows of
    # the others follow through their ON DELETE CASCADE.
    op.execute("""
        DELETE FROM search_index
        WHERE ctid IN (
            SELECT ranked.ctid
            FROM (
                SELECT
                    ctid,
                    row_number() OVER (
                        PARTITION BY project_id, permalink
                        ORDER BY type, id
                    ) AS row_rank
                FROM search_index
                WHERE permalink IS NOT NULL
            ) AS ranked
            WHERE ranked.row_rank > 1
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uix_search_index_permalink_project
        ON search_index (permalink, project_id)
        WHERE permalink IS NOT NULL
    """)
    op.execute("DROP INDEX IF EXISTS uix_search_index_permalink_type_project")
