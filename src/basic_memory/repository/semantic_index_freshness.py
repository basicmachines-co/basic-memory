"""How current semantic search is for a set of projects.

A note is saved and full-text indexed at once, but its embeddings come from a later
vector-sync pass. That pass can lag: a large note embeds a shard at a time, a failed
pass leaves work owed, and Basic Memory Cloud defers a legacy workspace's embeddings to
the next UTC day once it passes its fair-use budget. Search results say how far behind
the vectors are, so a client can tell "no semantic match" from "not embedded yet".

The answer is the newest last-modified time among notes whose embeddings are current:
the latest change semantic search reflects. It is reported only while some note is
behind. One note that stays behind (a failing embed, a stuck shard) therefore does not
date the whole index back to its own edit time. Derived state, read without locking,
per the consistency model in CLAUDE.md.
"""

from datetime import datetime

from sqlalchemy import Select, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.models.knowledge import ENTITY_EMBEDDING_NOT_CURRENT_PREDICATE, Entity
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_runtime import semantic_runtime_available
from basic_memory.utils import ensure_timezone_aware


def any_behind_statement(scope: ProjectScope) -> Select[tuple[bool]]:
    """Whether any of ``scope``'s notes has embeddings that lag its content.

    The predicate is the partial index's own text, so both engines can answer from
    `ix_entity_project_embedding_not_current`, which holds only the notes behind.
    """
    return select(
        exists().where(
            Entity.project_id.in_(scope.project_ids),
            text(ENTITY_EMBEDDING_NOT_CURRENT_PREDICATE),
        )
    )


def latest_embedded_statement(scope: ProjectScope) -> Select[tuple[datetime]]:
    """The newest last-modified time among ``scope``'s notes whose embeddings are current.

    "Current" is the exact negation of the not-current predicate: embedded at the note's
    present checksum, with no part of it left for a later pass.
    """
    return select(func.max(Entity.updated_at)).where(
        Entity.project_id.in_(scope.project_ids),
        Entity.content_type == "text/markdown",
        Entity.embedded_checksum == Entity.checksum,
        Entity.vector_sync_deferred_at.is_(None),
    )


def oldest_behind_statement(scope: ProjectScope) -> Select[tuple[datetime]]:
    """The oldest last-modified time among ``scope``'s notes whose embeddings lag."""
    return select(func.min(Entity.updated_at)).where(
        Entity.project_id.in_(scope.project_ids),
        text(ENTITY_EMBEDDING_NOT_CURRENT_PREDICATE),
    )


async def semantic_index_current_as_of(
    session_maker: async_sessionmaker[AsyncSession],
    app_config: BasicMemoryConfig,
    scope: ProjectScope,
) -> datetime | None:
    """Return the latest note change semantic search reflects for ``scope``, or None.

    None means every note's embeddings are current, or there is no semantic search to
    be current: a host that cannot load sqlite-vec runs keyword-only (#711), and an
    empty scope searched nothing.
    """
    if scope.is_empty or not semantic_runtime_available(app_config):
        return None

    async with db.scoped_session(session_maker) as session:
        # Trigger: no note is behind. Why: the common case, answered from the partial
        # index without touching current notes. Outcome: no freshness to report.
        if not (await session.execute(any_behind_statement(scope))).scalar_one():
            return None
        current_as_of = (await session.execute(latest_embedded_statement(scope))).scalar_one()
        # Trigger: notes are behind and none is embedded yet (a first pass still owed).
        # Why: there is no embedded change to point at, and null would claim every note
        # is current. Outcome: the oldest note behind, before which nothing is missing.
        if current_as_of is None:
            current_as_of = (await session.execute(oldest_behind_statement(scope))).scalar_one()
    # SQLite hands back naive local times. An aware value is what lets a client compare
    # answers from a local and a cloud database, as the all-projects search does.
    return ensure_timezone_aware(
        current_as_of, cloud_mode=app_config.database_backend == DatabaseBackend.POSTGRES
    )
