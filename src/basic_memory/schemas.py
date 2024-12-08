"""
Core pydantic models for basic-memory entities, observations, and relations.
These models define the schema for our core data types while remaining 
independent from storage/persistence concerns.
"""
from datetime import datetime, UTC
from typing import List, Optional, Dict, Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

class ObservationIn(BaseModel):
    """Schema for creating a single observation."""
    content: str
    context: Optional[str] = None

class ObservationsIn(BaseModel):
    """Schema for adding observations to an entity."""
    entity_id: str = Field(alias="entityId")  # Maps to Entity.id
    observations: List[ObservationIn]

    class Config:
        populate_by_name = True

class ObservationOut(ObservationIn):
    """Schema for observation data returned from the service."""
    id: int

class ObservationsOut(BaseModel):
    """Schema for bulk observation operation results."""
    entity_id: str = Field(alias="entityId")
    observations: List[ObservationOut]

    class Config:
        populate_by_name = True

class RelationIn(BaseModel):
    """
    Represents a directed edge between entities in the knowledge graph.
    Relations are always stored in active voice (e.g. "created", "teaches", etc.)
    """
    from_id: str = Field(alias="fromId")
    to_id: str = Field(alias="toId")              
    relation_type: str = Field(alias="relationType")
    context: Optional[str] = None

    class Config:
        populate_by_name = True

class RelationOut(BaseModel):
    id: int

class EntityBase(BaseModel):
    # id assigned at creation via model_validator
    id: str
    name: str
    entity_type: str = Field(alias="entityType")

    @model_validator(mode='before')
    @classmethod
    def generate_id(cls, data: dict) -> dict:
        """Generate an ID for this entity, eg `20240101-basic-memory`"""
        if not data.get('id') and data.get('name'):
            timestamp = datetime.now(UTC).strftime("%Y%m%d")
            normalized_name = data['name'].lower().replace(" ", "-")
            data['id'] = f"{timestamp}-{normalized_name}"
        return data

    def file_name(self) -> str:
        """Get the markdown file name for this entity."""
        return f"{self.id}.md"

class EntityIn(EntityBase):
    """
    Represents a node in our knowledge graph - could be a person, project,
    concept, etc. Each entity has a unique name, a type, and a list of
    associated observations.
    """
    observations: List[ObservationIn] = []
    relations: List[RelationIn] = []

    class Config:
        populate_by_name = True

class EntityOut(EntityBase):
    """Schema for entity data returned from the service."""
    observations: List[ObservationOut] = []
    relations: List[RelationOut] = []

    class Config:
        populate_by_name = True