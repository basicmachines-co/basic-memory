"""Optional semantic read-cache dependency.

Reads are served by ``read_cache.routes.cached_read`` dependencies. Routes and
services that mutate state use this dependency to retire the reads their change made
stale.
"""

from typing import Annotated

from fastapi import Depends, Request

from basic_memory.read_cache import ReadCacheInvalidator


async def get_read_cache(request: Request) -> ReadCacheInvalidator | None:
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


ReadCacheDep = Annotated[ReadCacheInvalidator | None, Depends(get_read_cache)]
