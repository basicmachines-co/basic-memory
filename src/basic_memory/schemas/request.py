"""Request schemas for interacting with the knowledge graph."""

from typing import List, Optional, Annotated, Literal
from annotated_types import MaxLen, MinLen

from pydantic import BaseModel, field_validator

from basic_memory.schemas.base import (
    Relation,
    Permalink,
)


class SearchNodesRequest(BaseModel):
    """Search for entities in the knowledge graph.

    The search looks across multiple fields:
    - Entity title
    - Entity types
    - summary
    - file content
    - Observations

    Features:
    - Case-insensitive matching
    - Partial word matches
    - Returns full entity objects with relations
    - Includes all matching entities
    - If a category is specified, only entities with that category are returned

    Example Queries:
    - "memory" - Find entities related to memory systems
    - "SQLite" - Find database-related components
    - "test" - Find test-related entities
    - "implementation" - Find concrete implementations
    - "service" - Find service components

    Note: Currently uses SQL ILIKE for matching. Wildcard (*) searches
    and full-text search capabilities are planned for future versions.
    """

    query: Annotated[str, MinLen(1), MaxLen(200)]
    category: Optional[str] = None


class GetEntitiesRequest(BaseModel):
    """Retrieve specific entities by their IDs.

    Used to load complete entity details including all observations
    and relations. Particularly useful for following relations
    discovered through search.
    """

    permalinks: Annotated[List[Permalink], MinLen(1), MaxLen(10)]


class CreateRelationsRequest(BaseModel):
    relations: List[Relation]


class EditEntityRequest(BaseModel):
    """Request schema for editing an existing entity's content.

    This allows for targeted edits without requiring the full entity content.
    Supports various operation types for different editing scenarios.
    """

    operation: Literal["append", "prepend", "find_replace", "replace_section"]
    content: str
    section: Optional[str] = None
    find_text: Optional[str] = None
    expected_replacements: int = 1

    @field_validator("section")
    @classmethod
    def validate_section_for_replace_section(cls, v, info):
        """Ensure section is provided for replace_section operation."""
        if info.data.get("operation") == "replace_section" and not v:
            raise ValueError("section parameter is required for replace_section operation")
        return v

    @field_validator("find_text")
    @classmethod
    def validate_find_text_for_find_replace(cls, v, info):
        """Ensure find_text is provided for find_replace operation."""
        if info.data.get("operation") == "find_replace" and not v:
            raise ValueError("find_text parameter is required for find_replace operation")
        return v


class MoveEntityRequest(BaseModel):
    """Request schema for moving an entity to a new file location.

    This allows moving notes to different paths while maintaining project
    consistency and optionally updating permalinks based on configuration.
    """

    identifier: Annotated[str, MinLen(1), MaxLen(200)]
    destination_path: Annotated[str, MinLen(1), MaxLen(500)]
    project: Optional[str] = None

    @field_validator("destination_path")
    @classmethod
    def validate_destination_path(cls, v):
        """Ensure destination path is relative and valid."""
        if v.startswith("/"):
            raise ValueError("destination_path must be relative, not absolute")
        if ".." in v:
            raise ValueError("destination_path cannot contain '..' path components")
        if not v.strip():
            raise ValueError("destination_path cannot be empty or whitespace only")
        return v.strip()
