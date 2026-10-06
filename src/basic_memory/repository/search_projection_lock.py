"""Serialize rewrites of one entity's search projection on Postgres.

Every refresh of an entity's search rows is delete-then-insert in one transaction:
delete every ``search_index`` row the entity owns, then insert the entity row, its
observation rows and its relation rows again. Two such transactions for the same
entity, running at once, collide in three ways under READ COMMITTED:

- the second DELETE waits on the first's row locks, then re-checks rows the first
  has already replaced, and the two wait on each other: ``deadlock detected``;
- the second DELETE's snapshot predates the first's INSERT, so it does not delete
  the new rows, and the second INSERT then hits ``search_index_pkey`` on
  ``(id, type, project_id)``. The upsert cannot absorb that: its conflict target is
  the permalink index, and Postgres raises a violation on any other unique index;
- the same, on relation rows, whose ids are the relation table's.

Saving one long note twice in quick succession is enough, because re-indexing a long
note takes longer than the gap between saves. Taking a transaction-scoped advisory
lock keyed on (project, entity) before the DELETE makes the rewrites of one entity
take turns, and the lock is released by the commit or rollback that ends the
transaction. Rewrites of different entities do not wait on each other.

Lock order: a transaction must take this lock before it touches any of the entity's
``search_index`` rows. Every writer that rewrites an entity's projection does, so a
holder of the lock never waits on another writer of those rows.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The key namespaces the lock, so it cannot collide with an advisory lock some other
# component takes on a bare (int, int) pair.
_LOCK_SQL = text(
    "SELECT pg_advisory_xact_lock("
    "hashtextextended('basic-memory:search-projection:' || :project_id || ':' || :entity_id, 0))"
)


async def lock_entity_search_projection(
    session: AsyncSession, *, project_id: int, entity_id: int
) -> None:
    """Wait until no other transaction is rewriting this entity's search rows.

    A no-op on SQLite, which already allows one writer at a time.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(_LOCK_SQL, {"project_id": str(project_id), "entity_id": str(entity_id)})
