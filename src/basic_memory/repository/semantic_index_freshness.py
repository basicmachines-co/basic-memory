"""How current semantic search is for a set of projects.

A note is saved and full-text indexed at once, but its embeddings come from a later
vector-sync pass. That pass can lag: a large note embeds a shard at a time, a failed
pass leaves work owed, and Basic Memory Cloud defers a legacy workspace's embeddings to
the next UTC day once it passes its fair-use budget. Search results say how far behind
the vectors are, so a client can tell "no semantic match" from "not embedded yet".

The answer is the oldest last-modified time among notes whose embeddings are not
current. Every note changed before it is reflected in vector search. Derived state,
read without locking, per the consistency model in CLAUDE.md.
"""

from datetime import datetime

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.models.knowledge import ENTITY_EMBEDDING_NOT_CURRENT_PREDICATE, Entity
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_runtime import semantic_runtime_available
from basic_memory.utils import ensure_timezone_aware


def oldest_behind_statement(scope: ProjectScope) -> Select[tuple[datetime]]:
    """The oldest last-modified time among ``scope``'s notes whose embeddings lag.

    The predicate is the partial index's own text, so both engines can answer from
    `ix_entity_project_embedding_not_current`, which holds only the notes behind.
    """
    return select(func.min(Entity.updated_at)).where(
        Entity.project_id.in_(scope.project_ids),
        text(ENTITY_EMBEDDING_NOT_CURRENT_PREDICATE),
    )


async def semantic_index_current_as_of(
    session_maker: async_sessionmaker[AsyncSession],
    app_config: BasicMemoryConfig,
    scope: ProjectScope,
) -> datetime | None:
    """Return when semantic search stopped being current for ``scope``, or None.

    None means every note's embeddings are current, or there is no semantic search to
    be current: a host that cannot load sqlite-vec runs keyword-only (#711), and an
    empty scope searched nothing.
    """
    if scope.is_empty or not semantic_runtime_available(app_config):
        return None

    async with db.scoped_session(session_maker) as session:
        oldest_behind = (await session.execute(oldest_behind_statement(scope))).scalar_one()
    if oldest_behind is None:
        return None
    # SQLite hands back naive local times. An aware value is what lets a client compare
    # answers from a local and a cloud database, as the all-projects search does.
    return ensure_timezone_aware(
        oldest_behind, cloud_mode=app_config.database_backend == DatabaseBackend.POSTGRES
    )
