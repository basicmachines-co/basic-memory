"""Configuration dependency injection for basic-memory.

Config enters the request DI graph from the composition root: the API lifespan
stores its container on ``app.state`` and this provider reads it back. Only
``ApiContainer.create()`` reads ConfigManager.
"""

from typing import Annotated

from fastapi import Depends, Request

from basic_memory.config import BasicMemoryConfig


async def get_app_config(request: Request) -> BasicMemoryConfig:
    """Resolve the application configuration from the composition root.

    API requests read the container the lifespan stored on ``app.state``.
    Requests served without a lifespan (the CLI/MCP local ASGI flow) fall back
    to the API composition root, which creates a container on demand.

    Declared ``async`` although it never awaits: FastAPI runs *sync* dependencies
    on anyio's worker-thread pool, and an attribute read is not worth a thread
    hop per request — nor the non-daemon worker it leaves parked (#1345).
    """
    container = getattr(request.app.state, "container", None)
    if container is not None:
        return container.config
    # Deferred import: importing basic_memory.api at module scope re-enters this
    # package via api.app -> routers -> deps and fails as a circular import.
    from basic_memory.api.container import resolve_container

    return resolve_container().config


AppConfigDep = Annotated[BasicMemoryConfig, Depends(get_app_config)]
