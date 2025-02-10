"""Repository for managing entities in the knowledge graph."""

from pathlib import Path
from typing import List, Optional, Sequence, Union

from sqlalchemy import select, or_, asc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption

from basic_memory.models.knowledge import Entity, Observation, Relation
from basic_memory.repository.repository import Repository


class EntityRepository(Repository[Entity]):
    """Repository for Entity model.
    
    Note: All file paths are stored as strings in the database. Convert Path objects
    to strings before passing to repository methods.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]):
        """Initialize with session maker."""
        super().__init__(session_maker, Entity)

    async def get_by_permalink(self, permalink: str) -> Optional[Entity]:
        """Get entity by permalink.
        
        Args:
            permalink: Unique identifier for the entity
        """
        query = self.select().where(Entity.permalink == permalink).options(*self.get_load_options())
        return await self.find_one(query)

    async def get_by_title(self, title: str) -> Optional[Entity]:
        """Get entity by title.
        
        Args:
            title: Title of the entity to find
        """
        query = self.select().where(Entity.title == title).options(*self.get_load_options())
        return await self.find_one(query)

    async def get_by_file_path(self, file_path: Union[Path, str]) -> Optional[Entity]:
        """Get entity by file_path.
        
        Args:
            file_path: Path to the entity file (will be converted to string internally)
        """
        query = self.select().where(Entity.file_path == str(file_path)).options(*self.get_load_options())
        return await self.find_one(query)

    async def list_entities(
        self,
        entity_type: Optional[str] = None,
        sort_by: Optional[str] = "updated_at",
        include_related: bool = False,
    ) -> Sequence[Entity]:
        """List all entities, optionally filtered by type and sorted.
        
        Args:
            entity_type: Optional type to filter by
            sort_by: Field to sort results by
            include_related: Whether to include related entities of the specified type
        """
        query = self.select()

        # Always load base relations
        query = query.options(*self.get_load_options())

        # Apply filters
        if entity_type:
            # When include_related is True, get both:
            # 1. Entities of the requested type
            # 2. Entities that have relations with entities of the requested type
            if include_related:
                query = query.where(
                    or_(
                        Entity.entity_type == entity_type,
                        Entity.outgoing_relations.any(
                            Relation.to_entity.has(entity_type=entity_type)
                        ),
                        Entity.incoming_relations.any(
                            Relation.from_entity.has(entity_type=entity_type)
                        ),
                    )
                )
            else:
                query = query.where(Entity.entity_type == entity_type)

        # Apply sorting
        if sort_by:
            sort_field = getattr(Entity, sort_by, Entity.updated_at)
            query = query.order_by(asc(sort_field))

        result = await self.execute_query(query)
        return list(result.scalars().all())

    async def get_entity_types(self) -> List[str]:
        """Get list of distinct entity types."""
        query = select(Entity.entity_type).distinct()

        result = await self.execute_query(query, use_query_options=False)
        return list(result.scalars().all())

    

    async def delete_entities_by_doc_id(self, doc_id: int) -> bool:
        """Delete all entities associated with a document."""
        return await self.delete_by_fields(doc_id=doc_id)

    async def delete_by_file_path(self, file_path: Union[Path, str]) -> bool:
        """Delete entity with the provided file_path.
        
        Args:
            file_path: Path to the entity file (will be converted to string internally)
        """
        return await self.delete_by_fields(file_path=str(file_path))

    def get_load_options(self) -> List[LoaderOption]:
        """Get SQLAlchemy loader options for eager loading relationships."""
        return [
            selectinload(Entity.observations).selectinload(Observation.entity),
            # Load from_relations and both entities for each relation
            selectinload(Entity.outgoing_relations).selectinload(Relation.from_entity),
            selectinload(Entity.outgoing_relations).selectinload(Relation.to_entity),
            # Load to_relations and both entities for each relation
            selectinload(Entity.incoming_relations).selectinload(Relation.from_entity),
            selectinload(Entity.incoming_relations).selectinload(Relation.to_entity),
        ]

    async def find_by_permalinks(self, permalinks: List[str]) -> Sequence[Entity]:
        """Find multiple entities by their permalink.
        
        Args:
            permalinks: List of permalink strings to find
        """
        # Handle empty input explicitly
        if not permalinks:
            return []

        # Use existing select pattern
        query = (
            self.select().options(*self.get_load_options()).where(Entity.permalink.in_(permalinks))
        )

        result = await self.execute_query(query)
        return list(result.scalars().all())

    async def delete_by_permalinks(self, permalinks: List[str]) -> int:
        """Delete multiple entities by permalink.
        
        Args:
            permalinks: List of permalink strings to delete
            
        Returns:
            Number of entities deleted
        """
        # Handle empty input explicitly
        if not permalinks:
            return 0

        # Find matching entities
        entities = await self.find_by_permalinks(permalinks)
        if not entities:
            return 0

        # Use existing delete_by_ids
        return await self.delete_by_ids([entity.id for entity in entities])