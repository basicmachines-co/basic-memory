"""V2 entity schemas with ID-first design."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from basic_memory.schemas.base import Observation, Relation


class EntityResolveRequest(BaseModel):
    """Request to resolve a string identifier to an entity ID.

    Supports resolution of:
    - Permalinks (e.g., "specs/search")
    - Titles (e.g., "Search Specification")
    - File paths (e.g., "specs/search.md")
    """

    identifier: str = Field(
        ...,
        description="Entity identifier to resolve (permalink, title, or file path)",
        min_length=1,
        max_length=500,
    )


class EntityResolveResponse(BaseModel):
    """Response from identifier resolution.

    Returns the entity ID and associated metadata for the resolved entity.
    """

    entity_id: int = Field(..., description="Numeric entity ID (primary identifier)")
    permalink: Optional[str] = Field(None, description="Entity permalink")
    file_path: str = Field(..., description="Relative file path")
    title: str = Field(..., description="Entity title")
    resolution_method: Literal["id", "permalink", "title", "path", "search"] = Field(
        ..., description="How the identifier was resolved"
    )


class EntityResponseV2(BaseModel):
    """V2 entity response with ID as the primary field.

    This response format emphasizes the entity ID as the primary identifier,
    with all other fields (permalink, file_path) as secondary metadata.
    """

    # ID first - this is the primary identifier in v2
    id: int = Field(..., description="Numeric entity ID (primary identifier)")

    # Core entity fields
    title: str = Field(..., description="Entity title")
    entity_type: str = Field(..., description="Entity type")
    content_type: str = Field(default="text/markdown", description="Content MIME type")

    # Secondary identifiers (for compatibility and convenience)
    permalink: Optional[str] = Field(None, description="Entity permalink (may change)")
    file_path: str = Field(..., description="Relative file path (may change)")

    # Content and metadata
    content: Optional[str] = Field(None, description="Entity content")
    entity_metadata: Optional[Dict] = Field(None, description="Entity metadata")

    # Relationships
    observations: List[Observation] = Field(default_factory=list, description="Entity observations")
    relations: List[Relation] = Field(default_factory=list, description="Entity relations")

    # Timestamps
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    # V2-specific metadata
    api_version: Literal["v2"] = Field(
        default="v2", description="API version (always 'v2' for this response)"
    )

    class Config:
        from_attributes = True
