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

from basic_memory.schemas.project_info import (
    ProjectStatistics,
    ActivityMetrics,
    SystemStatus,
    ProjectInfoResponse,
)

from basic_memory.schemas.directory import (
    DirectoryNode,
)

from basic_memory.schemas.sync_report import (
    SyncReportResponse,
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
    # Project Info
    "ProjectStatistics",
    "ActivityMetrics",
    "SystemStatus",
    "ProjectInfoResponse",
    # Directory
    "DirectoryNode",
    # Sync
    "SyncReportResponse",
]
