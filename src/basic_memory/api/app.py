"""FastAPI application for basic-memory knowledge graph API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from loguru import logger

from basic_memory import __version__ as version
from basic_memory.api.container import ApiContainer, set_container
from basic_memory.api.v2.routers import (
    knowledge_router as v2_knowledge,
    project_router as v2_project,
    memory_router as v2_memory,
    search_router as v2_search,
    resource_router as v2_resource,
    directory_router as v2_directory,
    prompt_router as v2_prompt,
    importer_router as v2_importer,
)
from basic_memory.config import init_api_logging
from basic_memory.services.initialization import initialize_app


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app. Not called in stdio mcp mode"""

    # Initialize logging for API (stdout in cloud mode, file otherwise)
    init_api_logging()

    # --- Composition Root ---
    # Create container and read config (single point of config access)
    container = ApiContainer.create()
    set_container(container)
    app.state.container = container

    logger.info(f"Starting Basic Memory API (mode={container.mode.name})")

    await initialize_app(container.config)

    # Cache database connections in app state for performance
    logger.info("Initializing database and caching connections...")
    engine, session_maker = await container.init_database()
    app.state.engine = engine
    app.state.session_maker = session_maker
    logger.info("Database connections cached in app state")

    # Create and start sync coordinator (lifecycle centralized in coordinator)
    sync_coordinator = container.create_sync_coordinator()
    await sync_coordinator.start()
    app.state.sync_coordinator = sync_coordinator

    # Proceed with startup
    yield

    # Shutdown - coordinator handles clean task cancellation
    logger.info("Shutting down Basic Memory API")
    await sync_coordinator.stop()

    await container.shutdown_database()


# Initialize FastAPI app
app = FastAPI(
    title="Basic Memory API",
    description="Knowledge graph API for basic-memory",
    version=version,
    lifespan=lifespan,
)

# Include v2 routers FIRST (more specific paths must match before /{project} catch-all)
app.include_router(v2_knowledge, prefix="/v2/projects/{project_id}")
app.include_router(v2_memory, prefix="/v2/projects/{project_id}")
app.include_router(v2_search, prefix="/v2/projects/{project_id}")
app.include_router(v2_resource, prefix="/v2/projects/{project_id}")
app.include_router(v2_directory, prefix="/v2/projects/{project_id}")
app.include_router(v2_prompt, prefix="/v2/projects/{project_id}")
app.include_router(v2_importer, prefix="/v2/projects/{project_id}")
app.include_router(v2_project, prefix="/v2")

# Legacy web app proxy paths (compat with /proxy/projects/projects)
app.include_router(v2_project, prefix="/proxy/projects")

# Legacy v1 compat: older CLI versions call GET /projects/projects
app.include_router(v2_project, prefix="/projects")

# V2 routers are the only public API surface


@app.exception_handler(Exception)
async def exception_handler(request, exc):  # pragma: no cover
    logger.exception(
        "API unhandled exception",
        url=str(request.url),
        method=request.method,
        client=request.client.host if request.client else None,
        path=request.url.path,
        error_type=type(exc).__name__,
        error=str(exc),
    )
    return await http_exception_handler(request, HTTPException(status_code=500, detail=str(exc)))
