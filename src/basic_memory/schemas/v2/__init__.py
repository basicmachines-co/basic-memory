"""V2 API schemas - ID-based entity and project references."""

from basic_memory.schemas.v2.entity import (
    EntityResolveRequest,
    EntityResolveResponse,
    EntityResponseV2,
    MoveEntityRequestV2,
    MoveDirectoryRequestV2,
    DeleteDirectoryRequestV2,
    IndexFileRequest,
    ProjectResolveRequest,
    ProjectResolveResponse,
)
from basic_memory.schemas.v2.graph import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    OrphanEntitiesResponse,
)
from basic_memory.schemas.v2.project_index import (
    ProjectIndexResponse,
    ProjectIndexStartedResponse,
)
from basic_memory.schemas.v2.resource import (
    CreateResourceRequest,
    UpdateResourceRequest,
    ResourceResponse,
)

__all__ = [
    "EntityResolveRequest",
    "EntityResolveResponse",
    "EntityResponseV2",
    "MoveEntityRequestV2",
    "MoveDirectoryRequestV2",
    "DeleteDirectoryRequestV2",
    "IndexFileRequest",
    "ProjectResolveRequest",
    "ProjectResolveResponse",
    "GraphEdge",
    "GraphNode",
    "GraphResponse",
    "OrphanEntitiesResponse",
    "ProjectIndexResponse",
    "ProjectIndexStartedResponse",
    "CreateResourceRequest",
    "UpdateResourceRequest",
    "ResourceResponse",
]
