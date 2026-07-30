"""Optional semantic read-cache dependency."""

from typing import Annotated

from fastapi import Depends, Request

from basic_memory.read_cache import ConfiguredReadCache, ReadCache
from basic_memory.read_cache.policy import (
    READ_CACHE_MAX_PAYLOAD_BYTES,
    READ_CACHE_TTL_SECONDS,
)


def get_read_cache(request: Request) -> ReadCache:
    """Return the host-injected cache or the container's no-op default."""
    try:
        container = request.app.state.container
    except AttributeError:
        pass
    else:
        return container.read_cache

    # Deferred import avoids api.app -> routers -> deps circular initialization.
    from basic_memory.api.container import resolve_container

    return resolve_container().read_cache


ReadCacheDep = Annotated[ReadCache, Depends(get_read_cache)]


def get_configured_read_cache(read_cache: ReadCacheDep) -> ConfiguredReadCache:
    """Bind the host cache to Basic Memory's API read policy."""
    return ConfiguredReadCache(
        backend=read_cache,
        ttl_seconds=READ_CACHE_TTL_SECONDS,
        max_payload_bytes=READ_CACHE_MAX_PAYLOAD_BYTES,
    )


ConfiguredReadCacheDep = Annotated[ConfiguredReadCache, Depends(get_configured_read_cache)]
