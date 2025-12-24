"""Repository for managing Relation objects."""

from typing import Sequence, List, Optional


from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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

    async def find_unresolved_relations_for_entity(self, entity_id: int) -> Sequence[Relation]:
        """Find unresolved relations for a specific entity.

        Args:
            entity_id: The entity whose unresolved outgoing relations to find.

        Returns:
            List of unresolved relations where this entity is the source.
        """
        query = select(Relation).filter(Relation.from_id == entity_id, Relation.to_id.is_(None))
        result = await self.execute_query(query)
        return result.scalars().all()

    async def add_all_ignore_duplicates(self, relations: List[Relation]) -> int:
        """Bulk insert relations, ignoring duplicates.

        Uses ON CONFLICT DO NOTHING to skip relations that would violate the
        unique constraint on (from_id, to_name, relation_type). This is useful
        for bulk operations where the same link may appear multiple times in
        a document.

        Works with both SQLite and PostgreSQL dialects.

        Args:
            relations: List of Relation objects to insert

        Returns:
            Number of relations actually inserted (excludes duplicates)
        """
        if not relations:
            return 0

        # Convert Relation objects to dicts for insert
        values = [
            {
                "project_id": r.project_id if r.project_id else self.project_id,
                "from_id": r.from_id,
                "to_id": r.to_id,
                "to_name": r.to_name,
                "relation_type": r.relation_type,
                "context": r.context,
            }
            for r in relations
        ]

        async with db.scoped_session(self.session_maker) as session:
            # Check dialect to use appropriate insert
            dialect_name = session.bind.dialect.name if session.bind else "sqlite"

            if dialect_name == "postgresql":
                # PostgreSQL: use RETURNING to count inserted rows
                # (rowcount is 0 for ON CONFLICT DO NOTHING)
                stmt = (
                    pg_insert(Relation)
                    .values(values)
                    .on_conflict_do_nothing()
                    .returning(Relation.id)
                )
                result = await session.execute(stmt)
                return len(result.fetchall())
            else:
                # SQLite: rowcount works correctly
                stmt = sqlite_insert(Relation).values(values)
                stmt = stmt.on_conflict_do_nothing()
                result = await session.execute(stmt)
                return result.rowcount if result.rowcount > 0 else 0

    def get_load_options(self) -> List[LoaderOption]:
        return [selectinload(Relation.from_entity), selectinload(Relation.to_entity)]
