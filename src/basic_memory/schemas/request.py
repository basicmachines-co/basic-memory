"""Request schemas for interacting with the knowledge graph."""

from typing import List, Optional, Annotated, Dict, Any
from annotated_types import MaxLen, MinLen

from pydantic import BaseModel

from basic_memory.schemas.base import (
    Relation,
    Permalink,
    EntityType,
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

    permalinks: Annotated[List[Permalink], MinLen(1)]


class CreateRelationsRequest(BaseModel):
    relations: List[Relation]


## update


# TODO remove UpdateEntityRequest
class UpdateEntityRequest(BaseModel):
    """Request to update an existing entity."""

    title: Optional[str] = None
    entity_type: Optional[EntityType] = None
    content: Optional[str] = None
    entity_metadata: Optional[Dict[str, Any]] = None
