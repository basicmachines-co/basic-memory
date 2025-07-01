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

from pydantic import BaseModel, ConfigDict, Field, AliasPath, AliasChoices

from basic_memory.schemas.base import Relation, Permalink, EntityType, ContentType, Observation


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

    from_id: Permalink = Field(
        # use the permalink from the associated Entity
        # or the from_id value
        validation_alias=AliasChoices(
            AliasPath("from_entity", "permalink"),
            "from_id",
        )
    )
    to_id: Optional[Permalink] = Field(  # pyright: ignore
        # use the permalink from the associated Entity
        # or the to_id value
        validation_alias=AliasChoices(
            AliasPath("to_entity", "permalink"),
            "to_id",
        ),
        default=None,
    )
    to_name: Optional[Permalink] = Field(
        # use the permalink from the associated Entity
        # or the to_id value
        validation_alias=AliasChoices(
            AliasPath("to_entity", "title"),
            "to_name",
        ),
        default=None,
    )


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
        "entity_type": "component",
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
    entity_type: EntityType
    entity_metadata: Optional[Dict] = None
    checksum: Optional[str] = None
    content_type: ContentType
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
                "entity_type": "component",
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
                "entity_type": "document",
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
                "entity_type": "component",
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
