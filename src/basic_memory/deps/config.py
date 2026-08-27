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

    This provider must stay ``async`` even though it never awaits. FastAPI runs
    *sync* dependencies in AnyIO's worker-thread pool, and those workers are
    non-daemon: after the request finishes AnyIO parks the thread on
    ``queue.Queue.get`` so it can be reused. Every CLI/MCP command drives the app
    through an in-process ASGI transport under ``asyncio.run``, which returns
    without joining that pool, so the interpreter reaches
    ``threading._shutdown`` with a live non-daemon thread that nothing will ever
    wake -- and the process hangs instead of exiting (observed as
    ``basic-memory schema validate`` never returning on Python 3.13; 3.14 reaps
    the stranded worker itself and masks the bug).

    Declaring it ``async`` keeps resolution on the event loop, so no worker
    thread is dispatched and nothing survives the request. See
    ``tests/test_app_config_dependency_resolution.py``.
    """
    container = getattr(request.app.state, "container", None)
    if container is not None:
        return container.config
    # Deferred import: importing basic_memory.api at module scope re-enters this
    # package via api.app -> routers -> deps and fails as a circular import.
    from basic_memory.api.container import resolve_container

    return resolve_container().config


AppConfigDep = Annotated[BasicMemoryConfig, Depends(get_app_config)]
