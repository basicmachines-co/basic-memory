"""Publishing the valid-time projection under the note_content generation fence.

Valid time is derived state: the markdown is the claim, these rows are its queryable
shadow, and every (re)index rebuilds them. Two properties keep that safe without adding
locks:

* A stale writer no-ops. It never deletes the current rows and never inserts its own.
* Observations and their valid time move together. The projection addresses observation
  rows by the ids the observation insert mints, so the two writes share one transaction
  under one held fence -- the narrow exception the publisher documents.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.models import IndexedObservation
from basic_memory.indexing.relation_persistence import RelationGenerationPublisher
from basic_memory.models import Entity, NoteContent
from basic_memory.repository.memory_time_index_repository import (
    AcceptedTemporalAssertion,
    MemoryTimeIndexRepository,
    TemporalGenerationWriteResult,
)
from basic_memory.repository.note_section_repository import NoteSectionRepository
from basic_memory.repository.observation_repository import (
    AcceptedObservationWrite,
    ObservationGenerationWriteResult,
    ObservationRepository,
)
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.schemas.search import SearchItemType
from basic_memory.temporal import (
    TemporalAssertion,
    TemporalRangeKind,
    TimeRole,
    parse_range_literal,
)

DATE = TemporalRangeKind.DATE


def _assertion(literal: str, role: TimeRole = TimeRole.EFFECTIVE) -> TemporalAssertion:
    return TemporalAssertion(
        time_role=role,
        valid_during=parse_range_literal(literal, kind=DATE),
        source_text=f"@{role.value}{literal}",
    )


def _accepted(
    source_id: int, literal: str, role: TimeRole = TimeRole.EFFECTIVE
) -> AcceptedTemporalAssertion:
    return AcceptedTemporalAssertion(
        source_type=SearchItemType.OBSERVATION.value,
        source_id=source_id,
        assertion=_assertion(literal, role),
    )


async def _add_note_content_generation(
    session_maker: async_sessionmaker[AsyncSession],
    entity: Entity,
    *,
    generation: int,
) -> None:
    """Give the entity a note_content row at `generation`, which is the fence."""
    async with db.scoped_session(session_maker) as session:
        session.add(
            NoteContent(
                entity_id=entity.id,
                project_id=entity.project_id,
                external_id=f"content-{entity.external_id}",
                file_path=entity.file_path,
                markdown_content="# Current\n",
                db_version=generation,
                db_checksum=f"checksum-{generation}",
                file_write_status="synced",
            )
        )


# --- Repository-level fence behavior ---


@pytest.mark.asyncio
async def test_temporal_projection_replaced_under_the_generation_fence(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """A later replace under the same fence discards every prior assertion."""
    repository = MemoryTimeIndexRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=3)

    async with db.scoped_session(session_maker) as session:
        await repository.replace_assertions_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=3,
            assertions=[_accepted(1, "[2026-06-10,2026-07-27)")],
        )
    async with db.scoped_session(session_maker) as session:
        result = await repository.replace_assertions_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=3,
            assertions=[
                _accepted(2, "[2026-07-27,)"),
                _accepted(3, "[2026-01-01,2026-06-10)", TimeRole.DUE),
            ],
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        rows = await repository.find_by_entity(session, sample_entity.id)

    assert [(row.source_id, row.time_role) for row in rows] == [(2, "effective"), (3, "due")]
    assert rows[0].lower_value == "2026-07-27"
    assert rows[0].upper_value is None


@pytest.mark.asyncio
async def test_stale_generation_leaves_temporal_rows_untouched(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """A stale writer no-ops instead of blocking: the current rows survive intact.

    This is the whole reason the fence exists rather than a lock. The loser of the race
    writes nothing and reports it, and the winner's projection stands.
    """
    repository = MemoryTimeIndexRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=8)
    async with db.scoped_session(session_maker) as session:
        await repository.replace_assertions_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=8,
            assertions=[_accepted(1, "[2026-07-27,)")],
        )

    async with db.scoped_session(session_maker) as session:
        result = await repository.replace_assertions_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=7,
            assertions=[_accepted(2, "[2026-01-01,2026-02-01)")],
        )

    assert not result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        rows = await repository.find_by_entity(session, sample_entity.id)
    assert [(row.source_id, row.lower_value) for row in rows] == [(1, "2026-07-27")]


@pytest.mark.asyncio
async def test_empty_assertion_set_wipes_prior_rows(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """Removing every qualifier from a note must remove its valid time, not keep it."""
    repository = MemoryTimeIndexRepository(project_id=sample_entity.project_id)
    await _add_note_content_generation(session_maker, sample_entity, generation=5)
    async with db.scoped_session(session_maker) as session:
        await repository.replace_assertions_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=5,
            assertions=[_accepted(1, "[2026-06-10,2026-07-27)")],
        )

    async with db.scoped_session(session_maker) as session:
        result = await repository.replace_assertions_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=5,
            assertions=[],
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        assert await repository.find_by_entity(session, sample_entity.id) == []


@pytest.mark.asyncio
async def test_find_for_sources_returns_nothing_for_an_empty_request(
    sample_entity: Entity,
    session_maker: async_sessionmaker[AsyncSession],
):
    """Hydrating an empty result page must not issue a query at all."""
    repository = MemoryTimeIndexRepository(project_id=sample_entity.project_id)

    async with db.scoped_session(session_maker) as session:
        assert await repository.find_for_sources(session, []) == []


# --- Publisher-level: observations and their valid time move together ---


@pytest.mark.asyncio
async def test_publisher_addresses_the_observation_rows_it_just_minted(
    sample_entity: Entity,
    observation_repository: ObservationRepository,
    relation_repository: RelationRepository,
    session_maker: async_sessionmaker[AsyncSession],
):
    """Each assertion lands on the id of the observation that carried the qualifier.

    Observation rows are wiped and recreated on every publication, so their ids only
    exist after the insert. Pairing by document order is what connects a qualifier back
    to its own statement rather than to its neighbour.
    """
    temporal_repository = MemoryTimeIndexRepository(project_id=sample_entity.project_id)
    publisher = RelationGenerationPublisher(
        relation_repository=relation_repository,
        observation_repository=observation_repository,
        section_repository=NoteSectionRepository(project_id=sample_entity.project_id),
        temporal_repository=temporal_repository,
        session_maker=session_maker,
    )
    await _add_note_content_generation(session_maker, sample_entity, generation=1)

    published = await publisher.publish(
        entity_id=sample_entity.id,
        generation=1,
        relations=[],
        observations=[
            IndexedObservation(
                content="The cache layer will use Redis.",
                category="decision",
                context=None,
                tags=None,
                temporal=(_assertion("[2026-06-10,2026-07-27)"),),
            ),
            IndexedObservation(
                content="The cache layer will use Memcached.",
                category="decision",
                context=None,
                tags=None,
                temporal=(_assertion("[2026-07-27,)"),),
            ),
            IndexedObservation(
                content="The queue layer will use RabbitMQ.",
                category="decision",
                context=None,
                tags=None,
            ),
        ],
    )

    assert published
    async with db.scoped_session(session_maker) as session:
        observations = await observation_repository.find_by_entity(session, sample_entity.id)
        rows = await temporal_repository.find_by_entity(session, sample_entity.id)

    ids_by_content = {observation.content: observation.id for observation in observations}
    assert {row.source_id: row.source_text for row in rows} == {
        ids_by_content["The cache layer will use Redis."]: "@effective[2026-06-10,2026-07-27)",
        ids_by_content["The cache layer will use Memcached."]: "@effective[2026-07-27,)",
    }
    # The undated observation contributes no row: it makes no claim.
    assert ids_by_content["The queue layer will use RabbitMQ."] not in {
        row.source_id for row in rows
    }
    assert all(row.source_type == SearchItemType.OBSERVATION.value for row in rows)


@pytest.mark.asyncio
async def test_republishing_rebuilds_the_projection_against_the_new_row_ids(
    sample_entity: Entity,
    observation_repository: ObservationRepository,
    relation_repository: RelationRepository,
    session_maker: async_sessionmaker[AsyncSession],
):
    """Re-indexing re-mints observation ids, and the projection follows them.

    This is the invariant that makes the shared transaction necessary: if the temporal
    write ran later, it would address ids the observation wipe had already discarded.
    """
    temporal_repository = MemoryTimeIndexRepository(project_id=sample_entity.project_id)
    publisher = RelationGenerationPublisher(
        relation_repository=relation_repository,
        observation_repository=observation_repository,
        section_repository=NoteSectionRepository(project_id=sample_entity.project_id),
        temporal_repository=temporal_repository,
        session_maker=session_maker,
    )
    await _add_note_content_generation(session_maker, sample_entity, generation=1)
    observation = IndexedObservation(
        content="The cache layer will use Redis.",
        category="decision",
        context=None,
        tags=None,
        temporal=(_assertion("[2026-06-10,2026-07-27)"),),
    )

    assert await publisher.publish(
        entity_id=sample_entity.id, generation=1, relations=[], observations=[observation]
    )
    assert await publisher.publish(
        entity_id=sample_entity.id, generation=1, relations=[], observations=[observation]
    )

    async with db.scoped_session(session_maker) as session:
        observations = await observation_repository.find_by_entity(session, sample_entity.id)
        rows = await temporal_repository.find_by_entity(session, sample_entity.id)

    assert [row.source_id for row in rows] == [observation.id for observation in observations]


@dataclass(slots=True)
class _MisalignedObservationStore:
    """An observation store that returns the wrong number of row ids."""

    calls: list[int] = field(default_factory=list)

    async def replace_observations_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        observations: Sequence[AcceptedObservationWrite],
    ) -> ObservationGenerationWriteResult:
        del session, entity_id, generation
        self.calls.append(len(observations))
        return ObservationGenerationWriteResult(generation_is_current=True, observation_ids=(1,))


@dataclass(slots=True)
class _UnreachableTemporalStore:
    """A temporal store that must never be called."""

    async def replace_assertions_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        assertions: Sequence[AcceptedTemporalAssertion],
    ) -> TemporalGenerationWriteResult:  # pragma: no cover - reaching this is the failure
        raise AssertionError("misaligned observation ids must be caught before publication")


@pytest.mark.asyncio
async def test_misaligned_observation_ids_fail_loudly(
    sample_entity: Entity,
    relation_repository: RelationRepository,
    session_maker: async_sessionmaker[AsyncSession],
):
    """Pairing by position is only safe while the two sequences agree in length.

    A mismatch would silently attach one statement's valid time to another, which is a
    wrong answer rather than a stale one -- so it raises instead of publishing.
    """
    publisher = RelationGenerationPublisher(
        relation_repository=relation_repository,
        observation_repository=_MisalignedObservationStore(),
        section_repository=NoteSectionRepository(project_id=sample_entity.project_id),
        temporal_repository=_UnreachableTemporalStore(),
        session_maker=session_maker,
    )
    await _add_note_content_generation(session_maker, sample_entity, generation=1)

    with pytest.raises(ValueError, match="returned 1 row ids for 2 observations"):
        await publisher.publish(
            entity_id=sample_entity.id,
            generation=1,
            relations=[],
            observations=[
                IndexedObservation("First", "decision", None, None),
                IndexedObservation("Second", "decision", None, None),
            ],
        )


@pytest.mark.asyncio
async def test_observation_write_returns_the_ids_it_minted(
    sample_entity: Entity,
    observation_repository: ObservationRepository,
    session_maker: async_sessionmaker[AsyncSession],
):
    """The observation replace reports its new row ids in document order."""
    await _add_note_content_generation(session_maker, sample_entity, generation=2)

    async with db.scoped_session(session_maker) as session:
        result = await observation_repository.replace_observations_for_generation(
            session,
            entity_id=sample_entity.id,
            generation=2,
            observations=[
                AcceptedObservationWrite("First", "decision", None, None),
                AcceptedObservationWrite("Second", "decision", None, None),
            ],
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        observations = await observation_repository.find_by_entity(session, sample_entity.id)
    assert result.observation_ids == tuple(observation.id for observation in observations)


@pytest.mark.asyncio
async def test_stale_observation_fence_publishes_no_valid_time(
    sample_entity: Entity,
    observation_repository: ObservationRepository,
    relation_repository: RelationRepository,
    session_maker: async_sessionmaker[AsyncSession],
):
    """A publication that lost its fence leaves both projections as they were."""
    temporal_repository = MemoryTimeIndexRepository(project_id=sample_entity.project_id)
    publisher = RelationGenerationPublisher(
        relation_repository=relation_repository,
        observation_repository=observation_repository,
        section_repository=NoteSectionRepository(project_id=sample_entity.project_id),
        temporal_repository=temporal_repository,
        session_maker=session_maker,
    )
    await _add_note_content_generation(session_maker, sample_entity, generation=9)

    published = await publisher.publish(
        entity_id=sample_entity.id,
        generation=4,
        relations=[],
        observations=[
            IndexedObservation(
                content="The cache layer will use Redis.",
                category="decision",
                context=None,
                tags=None,
                temporal=(_assertion("[2026-06-10,2026-07-27)"),),
            )
        ],
    )

    assert not published
    async with db.scoped_session(session_maker) as session:
        assert await temporal_repository.find_by_entity(session, sample_entity.id) == []
