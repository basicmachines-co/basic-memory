"""Repository for managing Observation objects."""

from typing import Dict, List, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption

from basic_memory.models import Observation
from basic_memory.repository.repository import Repository


class ObservationRepository(Repository[Observation]):
    """Repository for Observation model with memory-specific operations."""

    def __init__(self, project_id: int):
        """Initialize with project_id filter.

        Args:
            project_id: Project ID to filter all operations by
        """
        super().__init__(Observation, project_id=project_id)

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
