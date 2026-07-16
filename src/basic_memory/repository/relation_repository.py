"""Repository for managing Relation objects."""

from dataclasses import dataclass
from typing import Sequence, List, Optional, Any, cast

from sqlalchemy import and_, delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy.orm.interfaces import LoaderOption

from basic_memory.models import Relation, Entity
from basic_memory.repository.repository import Repository


@dataclass(frozen=True, slots=True)
class AcceptedRelationWrite:
    """One outgoing relation parsed from accepted markdown, ready to persist.

    Most targets are carried by name and left for forward-reference resolution.
    Safe self-relations can carry ``target_id`` because the general resolver
    deliberately skips them; persisting that ID in the accepted transaction
    keeps DB-first writes consistent with the normal indexing path (issue #1076).
    """

    relation_type: str
    target_name: str
    context: str | None
    target_id: int | None = None


class RelationRepository(Repository[Relation]):
    """Repository for Relation model with memory-specific operations."""

    def __init__(self, project_id: int):
        """Initialize with project_id filter.

        Args:
            project_id: Project ID to filter all operations by
        """
        super().__init__(Relation, project_id=project_id)

    async def find_relation(
        self,
        session: AsyncSession,
        from_permalink: str,
        to_permalink: str,
        relation_type: str,
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
        query = self._add_project_filter(query)
        return await self.find_one(session, query)

    async def find_by_entities(
        self, session: AsyncSession, from_id: int, to_id: int
    ) -> Sequence[Relation]:
        """Find all relations between two entities."""
        query = self.select().where((Relation.from_id == from_id) & (Relation.to_id == to_id))
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def find_by_type(self, session: AsyncSession, relation_type: str) -> Sequence[Relation]:
        """Find all relations of a specific type."""
        query = self.select().filter(Relation.relation_type == relation_type)
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def delete_outgoing_relations_from_entity(
        self, session: AsyncSession, entity_id: int
    ) -> None:
        """Delete outgoing relations for an entity.

        Only deletes relations where this entity is the source (from_id),
        as these are the ones owned by this entity's markdown file.
        """
        query = delete(Relation).where(Relation.from_id == entity_id)
        query = query.where(Relation.project_id == self.project_id)
        await session.execute(query)

    async def find_unresolved_relations(self, session: AsyncSession) -> Sequence[Relation]:
        """Find all unresolved relations, where to_id is null."""
        query = self.select().filter(Relation.to_id.is_(None))
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def find_unresolved_relations_for_entity(
        self, session: AsyncSession, entity_id: int
    ) -> Sequence[Relation]:
        """Find unresolved relations for a specific entity.

        Args:
            entity_id: The entity whose unresolved outgoing relations to find.

        Returns:
            List of unresolved relations where this entity is the source.
        """
        query = self.select().filter(Relation.from_id == entity_id, Relation.to_id.is_(None))
        result = await self.execute_query(session, query)
        return result.scalars().all()

    async def add_all_ignore_duplicates(
        self, session: AsyncSession, relations: List[Relation]
    ) -> int:
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

        # Check dialect to use appropriate insert
        dialect_name = session.bind.dialect.name if session.bind else "sqlite"

        if dialect_name == "postgresql":  # pragma: no cover
            # PostgreSQL: use RETURNING to count inserted rows
            # (rowcount is 0 for ON CONFLICT DO NOTHING)
            stmt = (  # pragma: no cover
                pg_insert(Relation).values(values).on_conflict_do_nothing().returning(Relation.id)
            )
            result = await session.execute(stmt)  # pragma: no cover
            return len(result.fetchall())  # pragma: no cover
        else:
            # SQLite: rowcount works correctly
            stmt = sqlite_insert(Relation).values(values)
            stmt = stmt.on_conflict_do_nothing()
            result = cast(CursorResult[Any], await session.execute(stmt))
            return result.rowcount if result.rowcount > 0 else 0

    async def replace_accepted_outgoing_relations(
        self,
        session: AsyncSession,
        entity_id: int,
        relations: Sequence[AcceptedRelationWrite],
    ) -> None:
        """Replace an entity's outgoing relations with the accepted markdown set.

        Delete-then-insert mirrors ``EntityService.update_entity_relations``:
        the markdown file owns its outgoing links, so an accepted write replaces
        the prior set. Ordinary targets are written unresolved and linked by the
        forward-reference job. Safe self-relations already carry their resolved
        ID because that job intentionally skips self targets. Runs inside the
        caller's transaction so the graph commits atomically with
        note_content/search (issue #1076).
        """
        await self.delete_outgoing_relations_from_entity(session, entity_id)
        if not relations:
            return
        rows = [
            Relation(
                project_id=self.project_id,
                from_id=entity_id,
                to_id=rel.target_id,
                to_name=rel.target_name,
                relation_type=rel.relation_type,
                context=rel.context,
            )
            for rel in relations
        ]
        # A single markdown file can repeat the same link; ignore-duplicates keeps
        # the unique (from_id, to_name, relation_type) constraint from aborting.
        await self.add_all_ignore_duplicates(session, rows)

    def get_load_options(self) -> List[LoaderOption]:
        return [selectinload(Relation.from_entity), selectinload(Relation.to_entity)]
