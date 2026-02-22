"""Response schemas for knowledge graph operations.

This module defines the response formats for all knowledge graph operations.
Each response includes complete information about the affected entities,
including IDs that can be used in subsequent operations.

Key Features:
1. Every created/updated object gets an ID
2. Relations are included with their parent entities
3. Responses include everything needed for next operations
4. Bulk operations return all affected items
"""

from datetime import datetime
from typing import List, Optional, Dict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from basic_memory.schemas.base import Relation, Permalink, NoteType, ContentType, Observation


class SQLAlchemyModel(BaseModel):
    """Base class for models that read from SQLAlchemy attributes.

    This base class handles conversion of SQLAlchemy model attributes
    to Pydantic model fields. All response models extend this to ensure
    proper handling of database results.
    """

    model_config = ConfigDict(from_attributes=True)


class ObservationResponse(Observation, SQLAlchemyModel):
    """Schema for observation data returned from the service.

    Each observation gets a unique ID that can be used for later
    reference or deletion.

    Example Response:
    {
        "category": "feature",
        "content": "Added support for async operations",
        "context": "Initial database design meeting"
    }
    """

    permalink: Permalink


class RelationResponse(Relation, SQLAlchemyModel):
    """Response schema for relation operations.

    Extends the base Relation model with a unique ID that can be
    used for later modification or deletion.

    Example Response:
    {
        "from_id": "test/memory_test",
        "to_id": "component/memory-service",
        "relation_type": "validates",
        "context": "Comprehensive test suite"
    }
    """

    permalink: Permalink

    # Override base Relation fields to allow Optional values
    from_id: Optional[Permalink] = Field(default=None)  # pyright: ignore[reportIncompatibleVariableOverride]
    to_id: Optional[Permalink] = Field(default=None)  # pyright: ignore[reportIncompatibleVariableOverride]
    to_name: Optional[str] = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def resolve_entity_references(cls, data):
        """Resolve from_id and to_id from joined entities, falling back to file_path.

        When loading from SQLAlchemy models, the from_entity and to_entity relationships
        are joined. We extract the permalink from these entities, falling back to
        file_path when permalink is None.

        We use file_path directly (not converted to permalink format) because if the
        entity doesn't have a permalink, the system won't be able to find it by a
        generated one anyway. Using the actual file_path preserves the real identifier.
        """
        # Handle dict input (e.g., from API or tests)
        if isinstance(data, dict):
            from_entity = data.get("from_entity")
            to_entity = data.get("to_entity")

            # Resolve from_id: prefer permalink, fall back to file_path
            if from_entity and isinstance(from_entity, dict):
                permalink = from_entity.get("permalink")
                if permalink:
                    data["from_id"] = permalink
                elif from_entity.get("file_path"):
                    data["from_id"] = from_entity["file_path"]

            # Resolve to_id: prefer permalink, fall back to file_path
            if to_entity and isinstance(to_entity, dict):
                permalink = to_entity.get("permalink")
                if permalink:
                    data["to_id"] = permalink
                elif to_entity.get("file_path"):
                    data["to_id"] = to_entity["file_path"]

                # Also resolve to_name from entity title
                if to_entity.get("title") and not data.get("to_name"):
                    data["to_name"] = to_entity["title"]

            return data

        # Handle SQLAlchemy model input (from_attributes=True)
        # Access attributes directly from the ORM model
        from_entity = getattr(data, "from_entity", None)
        to_entity = getattr(data, "to_entity", None)

        # Build a dict from the model's attributes
        result = {}

        # Copy base fields
        for field in ["permalink", "relation_type", "context", "to_name"]:
            if hasattr(data, field):
                result[field] = getattr(data, field)

        # Resolve from_id: prefer permalink, fall back to file_path
        if from_entity:
            permalink = getattr(from_entity, "permalink", None)
            file_path = getattr(from_entity, "file_path", None)
            if permalink:
                result["from_id"] = permalink
            elif file_path:
                result["from_id"] = file_path

        # Resolve to_id: prefer permalink, fall back to file_path
        if to_entity:
            permalink = getattr(to_entity, "permalink", None)
            file_path = getattr(to_entity, "file_path", None)
            if permalink:
                result["to_id"] = permalink
            elif file_path:
                result["to_id"] = file_path

            # Also resolve to_name from entity title if not set
            if not result.get("to_name"):
                title = getattr(to_entity, "title", None)
                if title:
                    result["to_name"] = title

        return result


class EntityResponse(SQLAlchemyModel):
    """Complete entity data returned from the service.

    This is the most comprehensive entity view, including:
    1. Basic entity details (id, name, type)
    2. All observations with their IDs
    3. All relations with their IDs
    4. Optional description

    Example Response:
    {
        "permalink": "component/memory-service",
        "file_path": "MemoryService",
        "note_type": "component",
        "entity_metadata": {}
        "content_type: "text/markdown"
        "observations": [
            {
                "category": "feature",
                "content": "Uses SQLite storage"
                "context": "Initial design"
            },
            {
                "category": "feature",
                "content": "Implements async operations"
                "context": "Initial design"
            }
        ],
        "relations": [
            {
                "from_id": "test/memory-test",
                "to_id": "component/memory-service",
                "relation_type": "validates",
                "context": "Main test suite"
            }
        ]
    }
    """

    permalink: Optional[Permalink]
    title: str
    file_path: str
    note_type: NoteType
    entity_metadata: Optional[Dict] = None
    checksum: Optional[str] = None
    content_type: ContentType
    external_id: Optional[str] = None
    observations: List[ObservationResponse] = []
    relations: List[RelationResponse] = []
    created_at: datetime
    updated_at: datetime


class EntityListResponse(SQLAlchemyModel):
    """Response for create_entities operation.

    Returns complete information about entities returned from the service,
    including their permalinks, observations,
    and any established relations.

    Example Response:
    {
        "entities": [
            {
                "permalink": "component/search_service",
                "title": "SearchService",
                "note_type": "component",
                "description": "Knowledge graph search",
                "observations": [
                    {
                        "content": "Implements full-text search"
                    }
                ],
                "relations": []
            },
            {
                "permalink": "document/api_docs",
                "title": "API_Documentation",
                "note_type": "document",
                "description": "API Reference",
                "observations": [
                    {
                        "content": "Documents REST endpoints"
                    }
                ],
                "relations": []
            }
        ]
    }
    """

    entities: List[EntityResponse]


class SearchNodesResponse(SQLAlchemyModel):
    """Response for search operation.

    Returns matching entities with their complete information,
    plus the original query for reference.

    Example Response:
    {
        "matches": [
            {
                "permalink": "component/memory-service",
                "title": "MemoryService",
                "note_type": "component",
                "description": "Core service",
                "observations": [...],
                "relations": [...]
            }
        ],
        "query": "memory"
    }

    Note: Each entity in matches includes full details
    just like EntityResponse.
    """

    matches: List[EntityResponse]
    query: str


class DeleteEntitiesResponse(SQLAlchemyModel):
    """Response indicating successful entity deletion.

    A simple boolean response confirming the delete operation
    completed successfully.

    Example Response:
    {
        "deleted": true
    }
    """

    deleted: bool


class DirectoryMoveError(BaseModel):
    """Error details for a failed file move within a directory move operation."""

    path: str
    error: str


class DirectoryDeleteError(BaseModel):
    """Error details for a failed file delete within a directory delete operation."""

    path: str
    error: str


class DirectoryMoveResult(SQLAlchemyModel):
    """Response schema for directory move operations.

    Returns detailed results of moving all files within a directory,
    including counts and any errors encountered.

    Example Response:
    {
        "total_files": 5,
        "successful_moves": 5,
        "failed_moves": 0,
        "moved_files": [
            "docs/file1.md",
            "docs/file2.md",
            "docs/subdir/file3.md"
        ],
        "errors": []
    }
    """

    total_files: int
    successful_moves: int
    failed_moves: int
    moved_files: List[str]  # List of file paths that were moved
    errors: List[DirectoryMoveError]  # List of errors for failed moves


class DirectoryDeleteResult(SQLAlchemyModel):
    """Response schema for directory delete operations.

    Returns detailed results of deleting all files within a directory,
    including counts and any errors encountered.

    Example Response:
    {
        "total_files": 5,
        "successful_deletes": 5,
        "failed_deletes": 0,
        "deleted_files": [
            "docs/file1.md",
            "docs/file2.md",
            "docs/subdir/file3.md"
        ],
        "errors": []
    }
    """

    total_files: int
    successful_deletes: int
    failed_deletes: int
    deleted_files: List[str]  # List of file paths that were deleted
    errors: List[DirectoryDeleteError]  # List of errors for failed deletes
