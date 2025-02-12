"""Knowledge graph schema exports.

This module exports all schema classes to simplify imports.
Rather than importing from individual schema files, you can
import everything from basic_memory.schemas.
"""

# Base types and models
from basic_memory.schemas.base import (
    Observation,
    EntityType,
    RelationType,
    Relation,
    Entity,
)

# Delete operation models
from basic_memory.schemas.delete import (
    DeleteEntitiesRequest,
)

# Request models
from basic_memory.schemas.request import (
    SearchNodesRequest,
    GetEntitiesRequest,
    CreateRelationsRequest,
    UpdateEntityRequest,
)

# Response models
from basic_memory.schemas.response import (
    SQLAlchemyModel,
    ObservationResponse,
    RelationResponse,
    EntityResponse,
    EntityListResponse,
    SearchNodesResponse,
    DeleteEntitiesResponse,
)

# Discovery and analytics models
from basic_memory.schemas.discovery import (
    EntityTypeList,
    ObservationCategoryList,
    TypedEntityList,
)

# For convenient imports, export all models
__all__ = [
    # Base
    "Observation",
    "EntityType",
    "RelationType",
    "Relation",
    "Entity",
    # Requests
    "SearchNodesRequest",
    "GetEntitiesRequest",
    "CreateRelationsRequest",
    "UpdateEntityRequest",
    # Responses
    "SQLAlchemyModel",
    "ObservationResponse",
    "RelationResponse",
    "EntityResponse",
    "EntityListResponse",
    "SearchNodesResponse",
    "DeleteEntitiesResponse",
    # Delete Operations
    "DeleteEntitiesRequest",
    # Discovery and Analytics
    "EntityTypeList",
    "ObservationCategoryList",
    "TypedEntityList",
]
