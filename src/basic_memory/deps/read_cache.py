"""Optional semantic read-cache dependency."""

from typing import Annotated

from fastapi import Depends, Request

from basic_memory.read_cache import ReadCache


def get_read_cache(request: Request) -> ReadCache:
    """Return the host-injected cache or the container's no-op default."""
    container = getattr(request.app.state, "container", None)
    if container is not None:
        return container.read_cache
    # Deferred import avoids api.app -> routers -> deps circular initialization.
    from basic_memory.api.container import resolve_container

    return resolve_container().read_cache


ReadCacheDep = Annotated[ReadCache, Depends(get_read_cache)]
