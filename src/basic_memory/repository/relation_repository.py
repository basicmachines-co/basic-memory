"""Repository for managing Relation objects."""

from sqlalchemy import and_, delete
from typing import Sequence, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy.orm.interfaces import LoaderOption

from basic_memory import db
from basic_memory.models import Relation, Entity
from basic_memory.repository.repository import Repository


class RelationRepository(Repository[Relation]):
    """Repository for Relation model with memory-specific operations."""

    def __init__(self, session_maker: async_sessionmaker, project_id: int):
        """Initialize with session maker and project_id filter.

        Args:
            session_maker: SQLAlchemy session maker
            project_id: Project ID to filter all operations by
        """
        super().__init__(session_maker, Relation, project_id=project_id)

    async def find_relation(
        self, from_permalink: str, to_permalink: str, relation_type: str
    ) -> Optional[Relation]:
        """Find a relation by its from and to path IDs."""
        from_entity = aliased(Entity)
        to_entity = aliased(Entity)

        query = (
            select(Relation)
            .join(from_entity, Relation.from_id == from_entity.id)
            .join(to_entity, Relation.to_id == to_entity.id)
            .where(
                and_(
                    from_entity.permalink == from_permalink,
                    to_entity.permalink == to_permalink,
                    Relation.relation_type == relation_type,
                )
            )
        )
        return await self.find_one(query)

    async def find_by_entities(self, from_id: int, to_id: int) -> Sequence[Relation]:
        """Find all relations between two entities."""
        query = select(Relation).where((Relation.from_id == from_id) & (Relation.to_id == to_id))
        result = await self.execute_query(query)
        return result.scalars().all()

    async def find_by_type(self, relation_type: str) -> Sequence[Relation]:
        """Find all relations of a specific type."""
        query = select(Relation).filter(Relation.relation_type == relation_type)
        result = await self.execute_query(query)
        return result.scalars().all()

    async def delete_outgoing_relations_from_entity(self, entity_id: int) -> None:
        """Delete outgoing relations for an entity.

        Only deletes relations where this entity is the source (from_id),
        as these are the ones owned by this entity's markdown file.
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(delete(Relation).where(Relation.from_id == entity_id))

    async def find_unresolved_relations(self) -> Sequence[Relation]:
        """Find all unresolved relations, where to_id is null."""
        query = select(Relation).filter(Relation.to_id.is_(None))
        result = await self.execute_query(query)
        return result.scalars().all()

    def get_load_options(self) -> List[LoaderOption]:
        return [selectinload(Relation.from_entity), selectinload(Relation.to_entity)]
