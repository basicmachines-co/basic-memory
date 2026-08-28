"""Optional semantic read-cache dependency."""

from typing import Annotated

from fastapi import Depends, Request
from pydantic import BaseModel

from basic_memory.read_cache import ModelReadCache, ReadCache
from basic_memory.read_cache.policy import (
    READ_CACHE_MAX_PAYLOAD_BYTES,
    READ_CACHE_TTL_SECONDS,
)


async def get_read_cache(request: Request) -> ReadCache | None:
    """Return the optional host-injected cache backend.

    ``async`` for the same reason as ``get_app_config``: a sync dependency would
    hop to anyio's worker pool just to read an attribute (#1345).
    """
    try:
        container = request.app.state.container
    except AttributeError:
        pass
    else:
        return container.read_cache

    # Deferred import avoids api.app -> routers -> deps circular initialization.
    from basic_memory.api.container import resolve_container

    return resolve_container().read_cache


ReadCacheDep = Annotated[ReadCache | None, Depends(get_read_cache)]


def create_model_read_cache[ModelT: BaseModel](
    read_cache: ReadCache | None,
    model_type: type[ModelT],
    *,
    ttl_seconds: int = READ_CACHE_TTL_SECONDS,
    max_payload_bytes: int = READ_CACHE_MAX_PAYLOAD_BYTES,
) -> ModelReadCache[ModelT] | None:
    """Bind one response model to the host cache and Basic Memory's read policy."""
    if read_cache is None:
        return None
    return ModelReadCache(
        backend=read_cache,
        model_type=model_type,
        ttl_seconds=ttl_seconds,
        max_payload_bytes=max_payload_bytes,
    )
