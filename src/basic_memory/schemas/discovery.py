"""Schemas for knowledge discovery and analytics endpoints."""

from typing import List, Optional
from pydantic import BaseModel, Field

from basic_memory.schemas.response import EntityResponse


class EntityTypeList(BaseModel):
    """List of unique entity types in the system."""

    types: List[str]


class ObservationCategoryList(BaseModel):
    """List of unique observation categories in the system."""

    categories: List[str]


class TypedEntityList(BaseModel):
    """List of entities of a specific type."""

    entity_type: str = Field(..., description="Type of entities in the list")
    entities: List[EntityResponse]
    total: int = Field(..., description="Total number of entities")
    sort_by: Optional[str] = Field(None, description="Field used for sorting")
    include_related: bool = Field(False, description="Whether related entities are included")
