"""V2 entity and project schemas with ID-first design."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict

from basic_memory.schemas.response import ObservationResponse, RelationResponse


class EntityResolveRequest(BaseModel):
    """Request to resolve a string identifier to an entity ID.

    Supports resolution of:
    - Permalinks (e.g., "specs/search")
    - Titles (e.g., "Search Specification")
    - File paths (e.g., "specs/search.md")

    When source_path is provided, resolution prefers notes closer to the source
    (context-aware resolution for duplicate titles).
    """

    identifier: str = Field(
        ...,
        description="Entity identifier to resolve (permalink, title, or file path)",
        min_length=1,
        max_length=500,
    )
    source_path: Optional[str] = Field(
        None,
        description="Path of the source file containing the link (for context-aware resolution)",
        max_length=500,
    )
    strict: bool = Field(
        False,
        description="If True, only exact matches are allowed (no fuzzy search fallback)",
    )


class EntityResolveResponse(BaseModel):
    """Response from identifier resolution.

    Returns the entity ID and associated metadata for the resolved entity.
    """

    external_id: str = Field(..., description="External UUID (primary API identifier)")
    entity_id: int = Field(..., description="Numeric entity ID (internal identifier)")
    permalink: Optional[str] = Field(None, description="Entity permalink")
    file_path: str = Field(..., description="Relative file path")
    title: str = Field(..., description="Entity title")
    resolution_method: Literal["external_id", "permalink", "title", "path", "search"] = Field(
        ..., description="How the identifier was resolved"
    )


class MoveEntityRequestV2(BaseModel):
    """V2 request schema for moving an entity to a new file location.

    In V2 API, the entity ID is provided in the URL path, so this request
    only needs the destination path.
    """

    destination_path: str = Field(
        ...,
        description="New file path for the entity (relative to project root)",
        min_length=1,
        max_length=500,
    )


class MoveDirectoryRequestV2(BaseModel):
    """V2 request schema for moving an entire directory to a new location.

    This moves all entities within a source directory to a destination directory
    while maintaining project consistency and updating database references.
    """

    source_directory: str = Field(
        ...,
        description="Source directory path (relative to project root)",
        min_length=1,
        max_length=500,
    )
    destination_directory: str = Field(
        ...,
        description="Destination directory path (relative to project root)",
        min_length=1,
        max_length=500,
    )


class DeleteDirectoryRequestV2(BaseModel):
    """V2 request schema for deleting all entities in a directory.

    This deletes all entities within a directory, removing them from the
    database and file system.
    """

    directory: str = Field(
        ...,
        description="Directory path to delete (relative to project root)",
        min_length=1,
        max_length=500,
    )


class EntityResponseV2(BaseModel):
    """V2 entity response with external_id as the primary API identifier.

    This response format emphasizes the external_id (UUID) as the primary API identifier,
    with the numeric id maintained for internal reference.
    """

    # External UUID first - this is the primary API identifier in v2
    external_id: str = Field(..., description="External UUID (primary API identifier)")
    # Internal numeric ID
    id: int = Field(..., description="Numeric entity ID (internal identifier)")

    # Core entity fields
    title: str = Field(..., description="Entity title")
    note_type: str = Field(..., description="Note type (from frontmatter 'type' field)")
    content_type: str = Field(default="text/markdown", description="Content MIME type")

    # Secondary identifiers (for compatibility and convenience)
    permalink: Optional[str] = Field(None, description="Entity permalink (may change)")
    file_path: str = Field(..., description="Relative file path (may change)")

    # Content and metadata
    content: Optional[str] = Field(None, description="Entity content")
    entity_metadata: Optional[Dict] = Field(None, description="Entity metadata")

    # Relationships
    observations: List[ObservationResponse] = Field(
        default_factory=list, description="Entity observations"
    )
    relations: List[RelationResponse] = Field(default_factory=list, description="Entity relations")

    # Timestamps
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    # V2-specific metadata
    api_version: Literal["v2"] = Field(
        default="v2", description="API version (always 'v2' for this response)"
    )

    model_config = ConfigDict(from_attributes=True)


class ProjectResolveRequest(BaseModel):
    """Request to resolve a project identifier to a project ID.

    Supports resolution of:
    - Project names (e.g., "my-project")
    - Permalinks (e.g., "my-project")
    """

    identifier: str = Field(
        ...,
        description="Project identifier to resolve (name or permalink)",
        min_length=1,
        max_length=255,
    )


class ProjectResolveResponse(BaseModel):
    """Response from project identifier resolution.

    Returns the project ID and associated metadata for the resolved project.
    """

    external_id: str = Field(..., description="External UUID (primary API identifier)")
    project_id: int = Field(..., description="Numeric project ID (internal identifier)")
    name: str = Field(..., description="Project name")
    permalink: str = Field(..., description="Project permalink")
    path: str = Field(..., description="Project file path")
    is_active: bool = Field(..., description="Whether the project is active")
    is_default: bool = Field(..., description="Whether the project is the default")
    resolution_method: Literal["external_id", "name", "permalink"] = Field(
        ..., description="How the identifier was resolved"
    )
