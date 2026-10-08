"""Recognize a search refresh that lost a race with another refresh of the same entity.

Every refresh of an entity's search rows is delete-then-insert in one transaction (#1623).
When two refreshes of the same entity overlap on Postgres, one of them fails in one of two
ways:

- ``deadlock detected``: the second DELETE re-checks rows the first transaction has
  already replaced, and the two transactions wait on each other;
- ``search_index_pkey``: both transactions reach their INSERTs together. The upsert's
  ``ON CONFLICT`` arbiter is the partial permalink index, so it cannot absorb a
  primary-key conflict on ``(id, type, project_id)``.

Either way the losing transaction rolls back to the projection the winner committed, and
search rows are derived state that the next refresh, ``reindex`` or the sweeper converges.
Callers use this to log the loss instead of failing the canonical write that triggered the
refresh. SQLite allows one writer at a time, so it never produces either error.
"""

from typing import Literal

from asyncpg.exceptions import DeadlockDetectedError, UniqueViolationError
from sqlalchemy.exc import DBAPIError

type SearchRefreshRace = Literal["deadlock", "search_index_pkey"]


def lost_search_refresh_race(error: BaseException) -> SearchRefreshRace | None:
    """Name the race a search refresh lost, or None when it failed for another reason."""
    if not isinstance(error, DBAPIError) or error.orig is None:
        return None
    # The asyncpg dialect raises its own adapted DBAPI error from the driver's error,
    # so the Postgres error class and constraint name live on the cause.
    match error.orig.__cause__:
        case DeadlockDetectedError():
            return "deadlock"
        case UniqueViolationError(constraint_name="search_index_pkey"):
            return "search_index_pkey"
        case _:
            return None
