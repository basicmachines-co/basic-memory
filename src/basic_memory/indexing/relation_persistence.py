"""Publish a note's derived graph under one accepted content generation.

The publication carries the complete observation, section, and relation projections
through one note_content fence lifecycle and one durable retry marker.

The graph tables are eventually consistent projections, not part of the accepted write's
transaction. Publication runs after the content commit; a stale fence makes every statement
a no-op (the newer generation owns convergence); a crashed or failed publication is repaired
by a later index pass republishing the same generation. Readers must tolerate a note whose
projections briefly lag its accepted content. This is deliberate: pulling these writes back
into one shared transaction — or adding cross-table locks to close the lag — is what caused
the #1213/#1214 deadlock and duplication incidents.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.models import IndexedObservation, IndexedRelation, IndexedSection
from basic_memory.repository.note_section_repository import (
    AcceptedSectionWrite,
    SectionGenerationWriteResult,
)
from basic_memory.repository.observation_repository import (
    AcceptedObservationWrite,
    ObservationGenerationWriteResult,
)
from basic_memory.repository.relation_repository import (
    RELATION_GENERATION_WRITE_STATEMENT_SIZE,
    AcceptedRelationWrite,
    RelationGenerationWriteResult,
)
from basic_memory.runtime.storage import ProjectId, RuntimeEntityId, RuntimeNoteContentVersion


class RelationGenerationStore(Protocol):
    """Repository operations needed to publish one relation generation."""

    async def begin_relation_generation_publication(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult: ...

    async def upsert_relation_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        relations: Sequence[AcceptedRelationWrite],
    ) -> RelationGenerationWriteResult: ...

    async def cleanup_relation_generations(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult: ...


class ObservationGenerationStore(Protocol):
    """Repository operation needed to replace one observation generation."""

    async def replace_observations_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        observations: Sequence[AcceptedObservationWrite],
    ) -> ObservationGenerationWriteResult: ...


class SectionGenerationStore(Protocol):
    """Repository operation needed to replace one section generation."""

    async def replace_sections_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        sections: Sequence[AcceptedSectionWrite],
    ) -> SectionGenerationWriteResult: ...


@dataclass(frozen=True, slots=True)
class RelationGenerationPublication:
    """Derived graph intent authorized by one accepted note-content generation."""

    project_id: ProjectId
    entity_id: RuntimeEntityId
    generation: RuntimeNoteContentVersion
    relations: tuple[IndexedRelation, ...]
    observations: tuple[IndexedObservation, ...] = ()
    sections: tuple[IndexedSection, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationGenerationPublisher:
    """Commit observations, sections, relation chunks, and cleanup under source fences."""

    relation_repository: RelationGenerationStore
    observation_repository: ObservationGenerationStore
    section_repository: SectionGenerationStore
    session_maker: async_sessionmaker[AsyncSession]

    async def publish(
        self,
        *,
        entity_id: int,
        generation: int,
        relations: Sequence[IndexedRelation],
        observations: Sequence[IndexedObservation] = (),
        sections: Sequence[IndexedSection] = (),
    ) -> bool:
        """Return whether every statement retained ownership of ``generation``."""
        relations_by_identity: dict[tuple[str, str], IndexedRelation] = {}
        for relation in relations:
            if relation.target_id is not None and relation.target_id != entity_id:
                raise ValueError("Only the source entity may be pre-resolved during publication")
            identity = relation.relation_type, relation.target_name
            relations_by_identity.setdefault(identity, relation)

        ordered_relations: list[AcceptedRelationWrite] = []
        resolved_identities: set[tuple[str, int]] = set()
        for _, relation in sorted(relations_by_identity.items()):
            if relation.target_id is not None:
                resolved_identity = relation.relation_type, relation.target_id
                # Constraint: safe aliases have distinct authored-name identities but share the
                # resolved relation uniqueness domain. Keep the lexical first alias so input order
                # cannot decide which valid source representation is published.
                if resolved_identity in resolved_identities:
                    continue
                resolved_identities.add(resolved_identity)
            ordered_relations.append(
                AcceptedRelationWrite(
                    relation_type=relation.relation_type,
                    target_name=relation.target_name,
                    context=relation.context,
                    target_id=relation.target_id,
                )
            )

        # Commit retry intent before the first independently committed chunk. If
        # any later statement fails, change detection will re-drive this source.
        async with db.scoped_session(self.session_maker) as session:
            publication = await self.relation_repository.begin_relation_generation_publication(
                session,
                entity_id=entity_id,
                generation=generation,
            )
        if not publication.generation_is_current:
            return False

        # Observations fit one statement batch, so their fenced replacement owns one short
        # transaction. An empty desired set must still execute to wipe the prior projection.
        accepted_observations = tuple(
            AcceptedObservationWrite(
                content=observation.content,
                category=observation.category,
                context=observation.context,
                tags=observation.tags,
            )
            for observation in observations
        )
        async with db.scoped_session(self.session_maker) as session:
            observation_result = (
                await self.observation_repository.replace_observations_for_generation(
                    session,
                    entity_id=entity_id,
                    generation=generation,
                    observations=accepted_observations,
                )
            )
        if not observation_result.generation_is_current:
            return False

        # Sections mirror the observation projection: one fenced wipe-and-recreate in
        # its own short transaction, where an empty desired set still wipes stale rows.
        accepted_sections = tuple(
            AcceptedSectionWrite(
                heading=section.heading,
                level=section.level,
                heading_path=section.heading_path,
                duplicate_index=section.duplicate_index,
                start_line=section.start_line,
                end_line=section.end_line,
                start_offset=section.start_offset,
                end_offset=section.end_offset,
            )
            for section in sections
        )
        async with db.scoped_session(self.session_maker) as session:
            section_result = await self.section_repository.replace_sections_for_generation(
                session,
                entity_id=entity_id,
                generation=generation,
                sections=accepted_sections,
            )
        if not section_result.generation_is_current:
            return False

        for relation_chunk in batched(
            ordered_relations,
            RELATION_GENERATION_WRITE_STATEMENT_SIZE,
        ):
            async with db.scoped_session(self.session_maker) as session:
                result = await self.relation_repository.upsert_relation_generation(
                    session,
                    entity_id=entity_id,
                    generation=generation,
                    relations=relation_chunk,
                )
            if not result.generation_is_current:
                return False

        # Cleanup is intentionally its own transaction. An empty desired set still
        # reaches this statement and removes every row older than the claimed generation.
        async with db.scoped_session(self.session_maker) as session:
            cleanup = await self.relation_repository.cleanup_relation_generations(
                session,
                entity_id=entity_id,
                generation=generation,
            )
        return cleanup.generation_is_current
