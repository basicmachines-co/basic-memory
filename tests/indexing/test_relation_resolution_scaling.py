"""PostgreSQL query-budget regression for project-wide relation resolution."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.indexing.relation_resolution import (
    RelationResolutionNoteContent,
    RepositoryRelationResolutionRuntime,
)
from basic_memory.models import Entity, Relation
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.services.bulk_link_resolver import BulkLinkResolver


class QueryBudgetExceeded(RuntimeError):
    """Raised as soon as a regression exceeds the production-shaped SQL budget."""


class EmptyNoteContentRepository:
    """No accepted-content rows are needed when every target stays unresolved."""

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> Sequence[RelationResolutionNoteContent]:
        del session, ids
        return ()


class RecordingEntityIndexer:
    """Record the one source refresh produced by the resolvable target."""

    def __init__(self) -> None:
        self.entity_ids: list[int] = []

    async def index_entities(
        self,
        entities: Sequence[Entity],
        *,
        content_by_entity_id: Mapping[int, str],
    ) -> None:
        assert content_by_entity_id == {}
        self.entity_ids.extend(entity.id for entity in entities)


@pytest.mark.asyncio
async def test_postgres_ten_thousand_targets_use_a_bounded_query_budget(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    entity_repository: EntityRepository,
    app_config: BasicMemoryConfig,
) -> None:
    """A production-sized missing-target pass must not issue one lookup sequence per target."""
    engine, session_maker = engine_factory
    if engine.dialect.name != "postgresql":
        pytest.skip("query-budget regression requires PostgreSQL")
    project_id = entity_repository.project_id
    assert project_id is not None

    now = datetime.now(timezone.utc)
    relation_repository = RelationRepository(project_id=project_id)
    async with db.scoped_session(session_maker) as session:
        source = await entity_repository.add(
            session,
            Entity(
                title="Relation Resolution Source",
                note_type="note",
                content_type="text/markdown",
                file_path="relation-resolution-source.md",
                permalink="relation-resolution-source",
                created_at=now,
                updated_at=now,
                project_id=project_id,
            ),
        )
        target = await entity_repository.add(
            session,
            Entity(
                title="Existing Target",
                note_type="note",
                content_type="text/markdown",
                file_path="existing-target.md",
                permalink="existing-target",
                created_at=now,
                updated_at=now,
                project_id=project_id,
            ),
        )
        for start in range(0, 10_000, 1_000):
            await relation_repository.add_all_ignore_duplicates(
                session,
                [
                    Relation(
                        project_id=project_id,
                        from_id=source.id,
                        to_id=None,
                        to_name=(
                            target.title
                            if target_index == 0
                            else f"Missing Target {target_index:05d}"
                        ),
                        relation_type="related_to",
                        context=None,
                    )
                    for target_index in range(start, start + 1_000)
                ],
            )

    entity_indexer = RecordingEntityIndexer()
    runtime = RepositoryRelationResolutionRuntime(
        session_maker=session_maker,
        relation_repository=relation_repository,
        entity_repository=entity_repository,
        note_content_repository=EmptyNoteContentRepository(),
        target_resolver=BulkLinkResolver(entity_repository, app_config),
        entity_indexer=entity_indexer,
    )

    query_count = 0
    query_budget = 30

    def count_query(*_: object) -> None:
        nonlocal query_count
        query_count += 1
        if query_count > query_budget:
            raise QueryBudgetExceeded(f"relation resolution exceeded {query_budget} SQL statements")

    event.listen(engine.sync_engine, "before_cursor_execute", count_query)
    try:
        assert await runtime.resolve_relations() == {source.id}
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_query)

    assert query_count <= query_budget
    assert entity_indexer.entity_ids == [source.id]

    async with db.scoped_session(session_maker) as session:
        remaining = await relation_repository.find_unresolved_relations(session)
    assert len(remaining) == 9_999

    entity_indexer.entity_ids.clear()
    assert await runtime.resolve_relations() == set()
    assert entity_indexer.entity_ids == []
