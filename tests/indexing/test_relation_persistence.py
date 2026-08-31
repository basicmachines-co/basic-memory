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
from basic_memory.indexing.models import IndexedObservation, IndexedRelation, IndexedSection
from basic_memory.indexing.relation_persistence import RelationGenerationPublisher
from basic_memory.models import Entity, Relation, RelationSearchRefresh
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import (
    AcceptedNoteContentWrite,
    NoteContentRepository,
)
from basic_memory.repository.note_section_repository import (
    AcceptedSectionWrite,
    NoteSectionRepository,
    SectionGenerationWriteResult,
)
from basic_memory.repository.observation_repository import (
    AcceptedObservationWrite,
    ObservationGenerationWriteResult,
    ObservationRepository,
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
    events: list[str] = field(default_factory=list)

    async def begin_relation_generation_publication(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult:
        assert session is not None
        self.events.append("begin")
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
        self.events.append("upsert")
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
        self.events.append("cleanup")
        self.calls.append(("cleanup", generation, ()))
        return RelationGenerationWriteResult(generation_is_current=self.generation_is_current)


@dataclass(slots=True)
class RecordingObservationGenerationStore:
    """Record the fenced observation replacement produced by the publisher."""

    generation_is_current: bool = True
    events: list[str] = field(default_factory=list)
    calls: list[tuple[int, tuple[AcceptedObservationWrite, ...]]] = field(default_factory=list)

    async def replace_observations_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        observations: Sequence[AcceptedObservationWrite],
    ) -> ObservationGenerationWriteResult:
        assert session is not None
        self.events.append("observations")
        self.calls.append((generation, tuple(observations)))
        return ObservationGenerationWriteResult(generation_is_current=self.generation_is_current)


@dataclass(slots=True)
class RecordingSectionGenerationStore:
    """Record the fenced section replacement produced by the publisher."""

    generation_is_current: bool = True
    events: list[str] = field(default_factory=list)
    calls: list[tuple[int, tuple[AcceptedSectionWrite, ...]]] = field(default_factory=list)

    async def replace_sections_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        sections: Sequence[AcceptedSectionWrite],
    ) -> SectionGenerationWriteResult:
        assert session is not None
        self.events.append("sections")
        self.calls.append((generation, tuple(sections)))
        return SectionGenerationWriteResult(generation_is_current=self.generation_is_current)


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
    observation_store = RecordingObservationGenerationStore(events=store.events)
    section_store = RecordingSectionGenerationStore(events=store.events)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        observation_repository=observation_store,
        section_repository=section_store,
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
    indexed_section = IndexedSection(
        heading="Decisions",
        level=2,
        heading_path="Auth/Decisions",
        duplicate_index=0,
        start_line=3,
        end_line=9,
        start_offset=12,
        end_offset=140,
    )

    generation_is_current = await publisher.publish(
        entity_id=42,
        generation=7,
        relations=relations,
        observations=[IndexedObservation("Observed", "note", None, ["graph"])],
        sections=[indexed_section],
    )

    assert generation_is_current
    assert store.events == ["begin", "observations", "sections", "upsert", "upsert", "cleanup"]
    assert observation_store.calls == [
        (7, (AcceptedObservationWrite("Observed", "note", None, ["graph"]),))
    ]
    assert section_store.calls == [
        (7, (AcceptedSectionWrite("Decisions", 2, "Auth/Decisions", 0, 3, 9, 12, 140),))
    ]
    assert [call[0] for call in store.calls] == ["begin", "upsert", "upsert", "cleanup"]
    assert [len(call[2]) for call in store.calls] == [0, 250, 1, 0]
    assert len(sessions) == 6
    assert len({id(session) for session in sessions}) == 6
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
    observation_store = RecordingObservationGenerationStore(events=store.events)
    section_store = RecordingSectionGenerationStore(events=store.events)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        observation_repository=observation_store,
        section_repository=section_store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    generation_is_current = await publisher.publish(
        entity_id=42,
        generation=6,
        relations=[IndexedRelation("links_to", "Target", None)],
    )

    assert not generation_is_current
    assert store.events == ["begin", "observations", "sections", "upsert"]
    assert [call[0] for call in store.calls] == ["begin", "upsert"]
    assert transaction_count == 4


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
    observation_store = RecordingObservationGenerationStore(events=store.events)
    section_store = RecordingSectionGenerationStore(events=store.events)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        observation_repository=observation_store,
        section_repository=section_store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    assert not await publisher.publish(
        entity_id=42,
        generation=6,
        relations=[IndexedRelation("links_to", "Target", None)],
    )
    assert [call[0] for call in store.calls] == ["begin"]
    assert observation_store.calls == []
    assert section_store.calls == []
    assert transaction_count == 1


@pytest.mark.asyncio
async def test_relation_generation_publisher_stops_when_observation_fence_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale observation replace stops before relation chunks or cleanup."""
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
    store = RecordingRelationGenerationStore()
    observation_store = RecordingObservationGenerationStore(
        generation_is_current=False,
        events=store.events,
    )
    section_store = RecordingSectionGenerationStore(events=store.events)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        observation_repository=observation_store,
        section_repository=section_store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    assert not await publisher.publish(
        entity_id=42,
        generation=6,
        relations=[IndexedRelation("links_to", "Target", None)],
        observations=[],
    )
    assert store.events == ["begin", "observations"]
    assert [call[0] for call in store.calls] == ["begin"]
    assert observation_store.calls == [(6, ())]
    assert section_store.calls == []
    assert transaction_count == 2


@pytest.mark.asyncio
async def test_relation_generation_publisher_stops_when_section_fence_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale section replace stops before relation chunks or cleanup."""
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
    store = RecordingRelationGenerationStore()
    observation_store = RecordingObservationGenerationStore(events=store.events)
    section_store = RecordingSectionGenerationStore(
        generation_is_current=False,
        events=store.events,
    )
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        observation_repository=observation_store,
        section_repository=section_store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    assert not await publisher.publish(
        entity_id=42,
        generation=6,
        relations=[IndexedRelation("links_to", "Target", None)],
        sections=[],
    )
    assert store.events == ["begin", "observations", "sections"]
    assert [call[0] for call in store.calls] == ["begin"]
    assert section_store.calls == [(6, ())]
    assert transaction_count == 3


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
    observation_store = RecordingObservationGenerationStore(events=store.events)
    section_store = RecordingSectionGenerationStore(events=store.events)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        observation_repository=observation_store,
        section_repository=section_store,
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
    assert observation_store.calls == [(7, ())]
    assert section_store.calls == [(7, ())]
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
    observation_store = RecordingObservationGenerationStore(events=store.events)
    section_store = RecordingSectionGenerationStore(events=store.events)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        observation_repository=observation_store,
        section_repository=section_store,
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
    observation_repository: ObservationRepository,
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
        observation_repository=observation_repository,
        section_repository=NoteSectionRepository(project_id=sample_entity.project_id),
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
        observation_repository=observation_repository,
        section_repository=NoteSectionRepository(project_id=sample_entity.project_id),
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


@pytest.mark.asyncio
async def test_generation_zero_relation_forces_generation_publication(
    sample_entity: Entity,
    entity_repository: EntityRepository,
    observation_repository: ObservationRepository,
    relation_repository: RelationRepository,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A rolling-deploy legacy write cannot make unchanged source bytes look complete."""
    content = "# Rolling relation write\n\n- links_to [[Target]]\n"
    checksum = sha256(content.encode()).hexdigest()
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
                updated_at=datetime.now(tz=UTC),
            ),
        )
        session.add(
            Relation(
                project_id=sample_entity.project_id,
                from_id=sample_entity.id,
                to_id=None,
                to_name="Target",
                relation_type="links_to",
                generation=0,
            )
        )

    change_detector = ChangeDetector(entity_repository, session_maker)
    assert await change_detector.load_indexed_file_checksums((sample_entity.file_path,)) == {
        sample_entity.file_path: None
    }

    publisher = RelationGenerationPublisher(
        relation_repository=relation_repository,
        observation_repository=observation_repository,
        section_repository=NoteSectionRepository(project_id=sample_entity.project_id),
        session_maker=session_maker,
    )
    assert await publisher.publish(
        entity_id=sample_entity.id,
        generation=1,
        relations=[IndexedRelation("links_to", "Target", None)],
    )

    assert await change_detector.load_indexed_file_checksums((sample_entity.file_path,)) == {
        sample_entity.file_path: checksum
    }
    async with db.scoped_session(session_maker) as session:
        relations = await relation_repository.find_by_type(session, "links_to")
    assert [(relation.to_name, relation.generation) for relation in relations] == [("Target", 1)]
