"""V2 API schemas - ID-based entity references."""

from basic_memory.schemas.v2.entity import (
    EntityResolveRequest,
    EntityResolveResponse,
    EntityResponseV2,
    MoveEntityRequestV2,
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
    "CreateResourceRequest",
    "UpdateResourceRequest",
    "ResourceResponse",
]
