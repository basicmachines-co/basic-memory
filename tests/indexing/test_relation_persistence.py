"""Tests for generation-owned relation publication orchestration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import AsyncIterator, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.change_detector import ChangeDetector
import basic_memory.indexing.relation_persistence as relation_persistence_module
from basic_memory.indexing.models import IndexedRelation
from basic_memory.indexing.relation_persistence import RelationGenerationPublisher
from basic_memory.models import Entity, RelationSearchRefresh
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import (
    AcceptedNoteContentWrite,
    NoteContentRepository,
)
from basic_memory.repository.relation_repository import (
    AcceptedRelationWrite,
    RelationRepository,
    RelationGenerationWriteResult,
)


@dataclass(slots=True)
class RecordingRelationGenerationStore:
    """Record the statement sequence produced by the publisher."""

    begin_is_current: bool = True
    generation_is_current: bool = True
    calls: list[tuple[str, int, tuple[AcceptedRelationWrite, ...]]] = field(default_factory=list)

    async def begin_relation_generation_publication(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult:
        assert session is not None
        self.calls.append(("begin", generation, ()))
        return RelationGenerationWriteResult(generation_is_current=self.begin_is_current)

    async def upsert_relation_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        relations: Sequence[AcceptedRelationWrite],
    ) -> RelationGenerationWriteResult:
        assert session is not None
        self.calls.append(("upsert", generation, tuple(relations)))
        return RelationGenerationWriteResult(generation_is_current=self.generation_is_current)

    async def cleanup_relation_generations(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult:
        assert session is not None
        self.calls.append(("cleanup", generation, ()))
        return RelationGenerationWriteResult(generation_is_current=self.generation_is_current)


@pytest.mark.asyncio
async def test_relation_generation_publisher_commits_sorted_chunks_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every bounded chunk and the final cleanup own a separate transaction."""
    sessions: list[AsyncSession] = []

    @asynccontextmanager
    async def fake_scoped_session(
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert session_maker is not None
        session = cast(AsyncSession, object())
        sessions.append(session)
        yield session

    monkeypatch.setattr(
        relation_persistence_module.db,
        "scoped_session",
        fake_scoped_session,
    )
    store = RecordingRelationGenerationStore()
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )
    relations = [
        IndexedRelation(
            relation_type="links_to",
            target_name=f"Target {index:03d}",
            context=None,
            target_id=42 if index == 0 else None,
        )
        for index in reversed(range(251))
    ]

    generation_is_current = await publisher.publish(
        entity_id=42,
        generation=7,
        relations=relations,
    )

    assert generation_is_current
    assert [call[0] for call in store.calls] == ["begin", "upsert", "upsert", "cleanup"]
    assert [len(call[2]) for call in store.calls] == [0, 250, 1, 0]
    published_names = [
        relation.target_name
        for operation, _, relation_chunk in store.calls
        if operation == "upsert"
        for relation in relation_chunk
    ]
    assert published_names == sorted(published_names)
    published_targets = [
        relation.target_id
        for operation, _, relation_chunk in store.calls
        if operation == "upsert"
        for relation in relation_chunk
    ]
    assert published_targets.count(42) == 1
    assert len(sessions) == 4
    assert len({id(session) for session in sessions}) == 4


@pytest.mark.asyncio
async def test_relation_generation_publisher_stops_when_source_fence_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost source claim cannot continue into later chunks or cleanup."""
    transaction_count = 0

    @asynccontextmanager
    async def fake_scoped_session(
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        nonlocal transaction_count
        assert session_maker is not None
        transaction_count += 1
        yield cast(AsyncSession, object())

    monkeypatch.setattr(
        relation_persistence_module.db,
        "scoped_session",
        fake_scoped_session,
    )
    store = RecordingRelationGenerationStore(generation_is_current=False)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    generation_is_current = await publisher.publish(
        entity_id=42,
        generation=6,
        relations=[IndexedRelation("links_to", "Target", None)],
    )

    assert not generation_is_current
    assert [call[0] for call in store.calls] == ["begin", "upsert"]
    assert transaction_count == 2


@pytest.mark.asyncio
async def test_relation_generation_publisher_stops_when_publication_cannot_begin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generation that is already stale cannot create chunks or cleanup work."""
    transaction_count = 0

    @asynccontextmanager
    async def fake_scoped_session(
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        nonlocal transaction_count
        assert session_maker is not None
        transaction_count += 1
        yield cast(AsyncSession, object())

    monkeypatch.setattr(
        relation_persistence_module.db,
        "scoped_session",
        fake_scoped_session,
    )
    store = RecordingRelationGenerationStore(begin_is_current=False)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    assert not await publisher.publish(
        entity_id=42,
        generation=6,
        relations=[IndexedRelation("links_to", "Target", None)],
    )
    assert [call[0] for call in store.calls] == ["begin"]
    assert transaction_count == 1


@pytest.mark.asyncio
async def test_relation_generation_publisher_deduplicates_pre_resolved_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safe source aliases cannot collide in the resolved relation uniqueness domain."""

    @asynccontextmanager
    async def fake_scoped_session(
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert session_maker is not None
        yield cast(AsyncSession, object())

    monkeypatch.setattr(
        relation_persistence_module.db,
        "scoped_session",
        fake_scoped_session,
    )
    store = RecordingRelationGenerationStore()
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    generation_is_current = await publisher.publish(
        entity_id=42,
        generation=7,
        relations=[
            IndexedRelation("links_to", "source/path", "path alias", target_id=42),
            IndexedRelation("links_to", "Source Title", "title alias", target_id=42),
            IndexedRelation("documents", "Source Title", None, target_id=42),
        ],
    )

    assert generation_is_current
    assert store.calls == [
        ("begin", 7, ()),
        (
            "upsert",
            7,
            (
                AcceptedRelationWrite("documents", "Source Title", None, target_id=42),
                AcceptedRelationWrite("links_to", "Source Title", "title alias", target_id=42),
            ),
        ),
        ("cleanup", 7, ()),
    ]


@pytest.mark.asyncio
async def test_relation_generation_publisher_rejects_non_self_pre_resolved_target() -> None:
    """Ordinary targets remain resolver-owned even when a caller supplies an ID."""
    store = RecordingRelationGenerationStore()
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    with pytest.raises(ValueError, match="Only the source entity"):
        await publisher.publish(
            entity_id=42,
            generation=7,
            relations=[IndexedRelation("links_to", "Target", None, target_id=99)],
        )

    assert store.calls == []


@pytest.mark.asyncio
async def test_failed_relation_publication_forces_change_detection_retry(
    sample_entity: Entity,
    entity_repository: EntityRepository,
    relation_repository: RelationRepository,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A committed retry marker re-drives unchanged bytes until publication completes."""
    content = "# Retry relation publication\n\n- links_to [[Target]]\n"
    checksum = sha256(content.encode()).hexdigest()
    observed_at = datetime.now(tz=UTC)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_id(
            session,
            sample_entity.id,
            load_relations=False,
        )
        assert entity is not None
        entity.checksum = checksum
        await NoteContentRepository(project_id=sample_entity.project_id).accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=sample_entity.id,
                markdown_content=content,
                db_version=1,
                db_checksum=checksum,
                last_source="test",
                updated_at=observed_at,
            ),
        )

    class _FailAfterPublicationBegins:
        async def begin_relation_generation_publication(
            self,
            session: AsyncSession,
            *,
            entity_id: int,
            generation: int,
        ) -> RelationGenerationWriteResult:
            return await relation_repository.begin_relation_generation_publication(
                session,
                entity_id=entity_id,
                generation=generation,
            )

        async def upsert_relation_generation(
            self,
            session: AsyncSession,
            *,
            entity_id: int,
            generation: int,
            relations: Sequence[AcceptedRelationWrite],
        ) -> RelationGenerationWriteResult:
            del session, entity_id, generation, relations
            raise OSError("relation chunk write failed")

        async def cleanup_relation_generations(
            self,
            session: AsyncSession,
            *,
            entity_id: int,
            generation: int,
        ) -> RelationGenerationWriteResult:
            del session, entity_id, generation
            raise AssertionError("failed publication cannot reach cleanup")

    failing_publisher = RelationGenerationPublisher(
        relation_repository=_FailAfterPublicationBegins(),
        session_maker=session_maker,
    )
    with pytest.raises(OSError, match="relation chunk write failed"):
        await failing_publisher.publish(
            entity_id=sample_entity.id,
            generation=1,
            relations=[IndexedRelation("links_to", "Target", None)],
        )

    change_detector = ChangeDetector(entity_repository, session_maker)
    assert await change_detector.load_indexed_file_checksums((sample_entity.file_path,)) == {
        sample_entity.file_path: None
    }
    async with db.scoped_session(session_maker) as session:
        publication_markers = list(
            (
                await session.scalars(
                    select(RelationSearchRefresh).where(
                        RelationSearchRefresh.entity_id == sample_entity.id
                    )
                )
            ).all()
        )
    assert [marker.publication_generation for marker in publication_markers] == [1]

    retry_publisher = RelationGenerationPublisher(
        relation_repository=relation_repository,
        session_maker=session_maker,
    )
    assert await retry_publisher.publish(
        entity_id=sample_entity.id,
        generation=1,
        relations=[IndexedRelation("links_to", "Target", None)],
    )
    assert await change_detector.load_indexed_file_checksums((sample_entity.file_path,)) == {
        sample_entity.file_path: checksum
    }
    async with db.scoped_session(session_maker) as session:
        refreshes = await relation_repository.list_pending_search_refreshes(
            session,
            entity_id=sample_entity.id,
        )
    assert [refresh.entity_id for refresh in refreshes] == [sample_entity.id, sample_entity.id]
