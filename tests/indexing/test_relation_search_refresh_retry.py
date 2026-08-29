"""Integration coverage for retryable relation-derived search refreshes."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.indexing.relation_resolution import (
    RelationSearchRefreshResult,
    RepositoryRelationResolutionRuntime,
)
from basic_memory.models import Entity, Relation, RelationSearchRefresh
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import (
    AcceptedNoteContentWrite,
    NoteContentRepository,
)
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.schemas.search import SearchItemType


class StaticLinkResolver:
    """Resolve only the explicit targets provided by the test."""

    def __init__(self, targets: Mapping[str, Entity]) -> None:
        self.targets = targets
        self.calls = 0

    async def resolve_relation_targets(
        self,
        link_texts: Sequence[str],
        *,
        session: AsyncSession,
    ) -> Mapping[str, Entity | None]:
        del session
        self.calls += len(link_texts)
        return {link_text: self.targets.get(link_text) for link_text in link_texts}


@pytest.mark.asyncio
async def test_clearing_observed_refresh_preserves_newer_work(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    session_maker,
    test_project,
) -> None:
    """A refresh committed during indexing survives retirement of the observed batch."""
    async with db.scoped_session(session_maker) as session:
        session.add(
            RelationSearchRefresh(
                project_id=test_project.id,
                entity_id=sample_entity.id,
            )
        )

    async with db.scoped_session(session_maker) as session:
        observed = await relation_repository.list_pending_search_refreshes(session)

    async with db.scoped_session(session_maker) as session:
        session.add(
            RelationSearchRefresh(
                project_id=test_project.id,
                entity_id=sample_entity.id,
            )
        )

    async with db.scoped_session(session_maker) as session:
        await relation_repository.clear_pending_search_refreshes(
            session,
            [refresh.id for refresh in observed],
        )

    async with db.scoped_session(session_maker) as session:
        remaining = await relation_repository.list_pending_search_refreshes(session)

    assert len(observed) == 1
    assert len(remaining) == 1
    assert remaining[0].id > observed[0].id


@pytest.mark.asyncio
async def test_relation_refresh_retries_after_search_write_failure_and_runtime_restart(
    entity_repository: EntityRepository,
    relation_repository: RelationRepository,
    search_service,
    session_maker,
    test_project,
    monkeypatch,
) -> None:
    """Committed relation work survives a failed refresh and a new runtime instance."""
    now = datetime.now(timezone.utc)
    source_content = "# Retry Source\n\nThe last valid search projection."
    source_checksum = sha256(source_content.encode("utf-8")).hexdigest()
    source = Entity(
        project_id=test_project.id,
        title="Retry Source",
        note_type="note",
        permalink="retry-source",
        file_path="retry-source.md",
        content_type="text/markdown",
        created_at=now,
        updated_at=now,
    )
    target = Entity(
        project_id=test_project.id,
        title="Retry Target",
        note_type="note",
        permalink="retry-target",
        file_path="retry-target.md",
        content_type="text/markdown",
        created_at=now,
        updated_at=now,
    )
    async with db.scoped_session(session_maker) as session:
        session.add_all([source, target])
        await session.flush()
        await NoteContentRepository(project_id=test_project.id).accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=source.id,
                markdown_content=source_content,
                db_version=1,
                db_checksum=source_checksum,
                last_source="test",
                updated_at=now,
            ),
        )
        session.add(
            Relation(
                project_id=test_project.id,
                from_id=source.id,
                to_id=None,
                to_name=target.title,
                relation_type="relates_to",
                generation=1,
            )
        )
        await session.flush()
        source_id = source.id
        target_id = target.id

    async with db.scoped_session(session_maker) as session:
        indexed_source = await entity_repository.find_by_id(session, source_id)
    assert indexed_source is not None
    await search_service.index_entity_data(indexed_source, content=source_content)

    refresh_attempts = 0
    original_index_entities = search_service.index_entities

    async def fail_once_index_entities(
        entities: Sequence[Entity],
        *,
        content_by_entity_id: Mapping[int, str],
    ) -> RelationSearchRefreshResult:
        nonlocal refresh_attempts
        assert [entity.id for entity in entities] == [source_id]
        assert content_by_entity_id == {source_id: source_content}
        refresh_attempts += 1
        if refresh_attempts == 1:
            raise OSError("transient search refresh failure")
        return await original_index_entities(
            entities,
            content_by_entity_id=content_by_entity_id,
        )

    monkeypatch.setattr(search_service, "index_entities", fail_once_index_entities)
    first_resolver = StaticLinkResolver({target.title: target})
    first_runtime = RepositoryRelationResolutionRuntime(
        session_maker=session_maker,
        relation_repository=relation_repository,
        entity_repository=entity_repository,
        note_content_repository=NoteContentRepository(project_id=test_project.id),
        target_resolver=first_resolver,
        entity_indexer=search_service,
    )

    with pytest.raises(OSError, match="transient search refresh failure"):
        await first_runtime.resolve_relations()

    async with db.scoped_session(session_maker) as session:
        resolved_relations = await relation_repository.find_by_type(session, "relates_to")
        pending_refreshes = await relation_repository.list_pending_search_refreshes(session)
        filtered_refreshes = await relation_repository.list_pending_search_refreshes(
            session,
            entity_id=source_id,
        )
    assert [(relation.from_id, relation.to_id) for relation in resolved_relations] == [
        (source_id, target_id)
    ]
    assert [refresh.entity_id for refresh in pending_refreshes] == [source_id]
    assert filtered_refreshes == pending_refreshes

    rows_after_failure = await search_service.repository.search(
        search_item_types=[SearchItemType.ENTITY],
    )
    assert any(
        row.entity_id == source_id and row.content_snippet == source_content
        for row in rows_after_failure
    )

    # A fresh runtime has no in-memory knowledge of the first attempt. It must
    # discover the committed refresh marker even though no relation is unresolved.
    retry_resolver = StaticLinkResolver({})
    retry_runtime = RepositoryRelationResolutionRuntime(
        session_maker=session_maker,
        relation_repository=RelationRepository(project_id=test_project.id),
        entity_repository=EntityRepository(project_id=test_project.id),
        note_content_repository=NoteContentRepository(project_id=test_project.id),
        target_resolver=retry_resolver,
        entity_indexer=search_service,
    )

    affected = await retry_runtime.resolve_relations()

    assert affected == {source_id}
    assert retry_resolver.calls == 0
    assert refresh_attempts == 2
    async with db.scoped_session(session_maker) as session:
        remaining_refreshes = await relation_repository.list_pending_search_refreshes(session)
    assert remaining_refreshes == []

    relation_rows = await search_service.repository.search(
        search_item_types=[SearchItemType.RELATION],
    )
    source_relation_rows = [row for row in relation_rows if row.entity_id == source_id]
    assert len(source_relation_rows) == 1
    assert source_relation_rows[0].to_id == target_id
