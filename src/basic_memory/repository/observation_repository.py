"""Repository for managing Observation objects."""

from dataclasses import dataclass
from typing import override, Dict, List, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption

from basic_memory.models import Observation
from basic_memory.repository.relation_repository import current_relation_generation_statement
from basic_memory.repository.repository import Repository
from basic_memory.temporal import TemporalAssertion


@dataclass(frozen=True, slots=True)
class AcceptedObservationWrite:
    """One observation parsed from accepted markdown, ready to persist.

    Mirrors the markdown ``Observation`` fields so the accepted-write path can
    persist the graph without constructing ORM rows in the storage-neutral
    runner (issue #1076).

    ``temporal`` rides along rather than becoming observation columns: authored
    valid time is its own projection keyed on the row this write mints, and an
    observation may carry several assertions (SPEC-82).
    """

    content: str
    category: str | None
    context: str | None
    tags: list[str] | None
    temporal: tuple[TemporalAssertion, ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationGenerationWriteResult:
    """Whether a guarded observation replacement still owned its source generation.

    ``observation_ids`` are the freshly minted row ids in document order, aligned
    with the sequence that was written. The temporal projection addresses those
    rows, and they only exist once the insert has flushed.
    """

    generation_is_current: bool
    observation_ids: tuple[int, ...] = ()


class ObservationRepository(Repository[Observation]):
    """Repository for Observation model with memory-specific operations."""

    project_id: int

    def __init__(self, project_id: int):
        """Initialize with project_id filter.

        Args:
            project_id: Project ID to filter all operations by
        """
        super().__init__(Observation, project_id=project_id)

    @override
    def get_load_options(self) -> List[LoaderOption]:
        """Eager-load parent entity to prevent N+1 if obs.entity is accessed."""
        return [selectinload(Observation.entity)]

    async def find_by_entity(self, session: AsyncSession, entity_id: int) -> Sequence[Observation]:
        """Find all observations for a specific entity."""
        query = self.select().filter(Observation.entity_id == entity_id)
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def find_by_context(self, session: AsyncSession, context: str) -> Sequence[Observation]:
        """Find observations with a specific context."""
        query = self.select().filter(Observation.context == context)
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def find_by_category(self, session: AsyncSession, category: str) -> Sequence[Observation]:
        """Find observations with a specific context."""
        query = self.select().filter(Observation.category == category)
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def observation_categories(self, session: AsyncSession) -> Sequence[str]:
        """Return a list of all observation categories."""
        query = select(Observation.category).distinct()
        query = self._add_project_filter(query)
        result = await self.execute_query(session, query, use_query_options=False)
        return result.scalars().all()

    async def find_by_entities(
        self, session: AsyncSession, entity_ids: List[int]
    ) -> Dict[int, List[Observation]]:
        """Find all observations for multiple entities in a single query.

        Args:
            entity_ids: List of entity IDs to fetch observations for

        Returns:
            Dictionary mapping entity_id to list of observations
        """
        if not entity_ids:  # pragma: no cover
            return {}

        # Query observations for all entities in the list
        query = self.select().filter(Observation.entity_id.in_(entity_ids))
        result = await self.execute_query(session, query)
        observations = result.scalars().all()

        # Group observations by entity_id
        observations_by_entity = {}
        for obs in observations:
            if obs.entity_id not in observations_by_entity:
                observations_by_entity[obs.entity_id] = []
            observations_by_entity[obs.entity_id].append(obs)

        return observations_by_entity

    async def replace_observations_for_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        observations: Sequence[AcceptedObservationWrite],
    ) -> ObservationGenerationWriteResult:
        """Replace observations only while the accepted content generation is current."""
        # This helper is a shared note_content fence despite its historical relation name.
        current_generation = await session.scalar(
            current_relation_generation_statement(
                project_id=self.project_id,
                entity_id=entity_id,
                generation=generation,
            )
        )
        # Trigger: a newer accepted note generation won before this transaction acquired the row.
        # Why: replacing here would publish stale observations over the newer Markdown projection.
        # Outcome: leave every existing observation untouched and let the current writer publish.
        if current_generation is None:
            return ObservationGenerationWriteResult(generation_is_current=False)

        await self.delete_by_fields(session, entity_id=entity_id)
        rows = [
            Observation(
                project_id=self.project_id,
                entity_id=entity_id,
                content=obs.content,
                category=obs.category,
                context=obs.context,
                tags=obs.tags,
            )
            for obs in observations
        ]
        await self.add_all_no_return(session, rows)
        # add_all_no_return flushes, so every row now carries its database id.
        # Reading them here, inside the same transaction, is what lets the temporal
        # projection address these exact rows.
        return ObservationGenerationWriteResult(
            generation_is_current=True,
            observation_ids=tuple(row.id for row in rows),
        )
