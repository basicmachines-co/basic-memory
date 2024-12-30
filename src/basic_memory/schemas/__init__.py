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
    DeleteRelationsRequest,
    DeleteObservationsRequest,
)

# Request models
from basic_memory.schemas.request import (
    AddObservationsRequest,
    CreateEntityRequest,
    SearchNodesRequest,
    OpenNodesRequest,
    CreateRelationsRequest,
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
    "AddObservationsRequest",
    "CreateEntityRequest",
    "SearchNodesRequest",
    "OpenNodesRequest",
    "CreateRelationsRequest",
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
    "DeleteRelationsRequest",
    "DeleteObservationsRequest",
    # Discovery and Analytics
    "EntityTypeList",
    "ObservationCategoryList",
]
