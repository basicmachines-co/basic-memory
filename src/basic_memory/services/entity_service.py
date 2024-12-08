"""Service for managing entities in the database."""
from datetime import datetime, UTC
from pathlib import Path

from basic_memory.repository import EntityRepository
from basic_memory.schemas import EntityIn, ObservationIn
from basic_memory.models import Entity, Observation
from basic_memory.fileio import EntityNotFoundError
from . import ServiceError


class EntityService:
    """
    Service for managing entities in the database.
    File operations are handled by MemoryService.
    """
    
    def __init__(self, project_path: Path, entity_repo: EntityRepository):
        self.project_path = project_path
        self.entity_repo = entity_repo

    async def create_entity(self, entity: EntityIn) -> Entity:
        """Create a new entity in the database.
        
        Note: ID is generated by the EntityIn validator before reaching this method.
        """
        # Create base entity first
        base_data = {
            "id": entity.id,  # Include the generated ID
            "name": entity.name,
            "entity_type": entity.entity_type,
            "created_at": datetime.now(UTC),
        }
        created_entity = await self.entity_repo.create(base_data)
        await self.entity_repo.refresh(created_entity, ['observations', 'outgoing_relations', 'incoming_relations'])
        return created_entity

    async def get_entity(self, entity_id: str) -> Entity:
        """Get entity by ID."""
        db_entity = await self.entity_repo.find_by_id(entity_id)
        if not db_entity:
            raise EntityNotFoundError(f"Entity not found: {entity_id}")
            
        return db_entity

    # TODO name is not unique
    async def get_by_name(self, name: str) -> Entity:
        """Get entity by name."""
        db_entity = await self.entity_repo.find_by_name(name)
        if not db_entity:
            raise EntityNotFoundError(f"Entity not found: {name}")
            
        return db_entity

    async def delete_entity(self, entity_id: str) -> bool:
        """Delete entity from database."""
        return await self.entity_repo.delete(entity_id)