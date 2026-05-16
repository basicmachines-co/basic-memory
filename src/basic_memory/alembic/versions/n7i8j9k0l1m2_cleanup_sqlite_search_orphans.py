"""Remove orphaned search rows whose project was already deleted.

Revision ID: n7i8j9k0l1m2
Revises: m6h7i8j9k0l1
Create Date: 2026-05-15 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "n7i8j9k0l1m2"
down_revision: Union[str, None] = "m6h7i8j9k0l1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(connection, table_name: str) -> bool:
    """Inspector-based table check, dialect agnostic.

    Trigger: SQLite creates search_index as an FTS5 virtual table at runtime
    via SearchRepository.init_search_index, not through Alembic, so fresh
    installs hit this migration before the table exists.
    Why: a blind DELETE against a missing table fails the whole upgrade.
    Outcome: callers skip the sweep when the table isn't present yet — the
    runtime-created table on a fresh DB has no orphans to clean.
    """
    return table_name in inspect(connection).get_table_names()


def upgrade() -> None:
    """Purge orphaned search rows left over from prior project deletions.

    Trigger: project deletion on SQLite never removed the derived FTS rows,
    because the FTS5 virtual table can't carry a foreign key. The leak shows
    up in two shapes:
    1. project_id no longer exists in `project` (deleted project, id never
       reused).
    2. project_id still exists but `entity_id` no longer exists in `entity`
       — auto-increment handed the id to a brand-new project and the FTS
       rows from the deleted predecessor masquerade as the new tenant's data.
    Why: search_index.project_id is the only scope predicate the search
    repository applies, so leftover rows surface under the wrong project on
    every search.
    Outcome: a one-time sweep deletes both shapes, from the FTS index and
    from search_vector_chunks. Postgres already cascaded on FK delete, so
    these statements are no-ops there.
    """
    connection = op.get_bind()

    if _table_exists(connection, "search_index"):
        op.execute(
            """
            DELETE FROM search_index
            WHERE project_id NOT IN (SELECT id FROM project)
            """
        )
        op.execute(
            """
            DELETE FROM search_index
            WHERE entity_id IS NOT NULL
              AND entity_id NOT IN (SELECT id FROM entity)
            """
        )

    if _table_exists(connection, "search_vector_chunks"):
        op.execute(
            """
            DELETE FROM search_vector_chunks
            WHERE project_id NOT IN (SELECT id FROM project)
            """
        )
        op.execute(
            """
            DELETE FROM search_vector_chunks
            WHERE entity_id NOT IN (SELECT id FROM entity)
            """
        )


def downgrade() -> None:
    """No-op: orphan rows cannot be reconstructed."""
    pass
