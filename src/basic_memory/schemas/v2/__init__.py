"""V2 API schemas - ID-based entity and project references."""

from basic_memory.schemas.v2.entity import (
    EntityResolveRequest,
    EntityResolveResponse,
    EntityResponseV2,
    MoveEntityRequestV2,
    ProjectResolveRequest,
    ProjectResolveResponse,
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
    "ProjectResolveRequest",
    "ProjectResolveResponse",
    "CreateResourceRequest",
    "UpdateResourceRequest",
    "ResourceResponse",
]
