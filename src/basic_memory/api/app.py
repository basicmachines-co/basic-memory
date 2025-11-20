"""FastAPI application for basic-memory knowledge graph API."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from loguru import logger

from basic_memory import __version__ as version
from basic_memory import db
from basic_memory.api.routers import (
    directory_router,
    importer_router,
    knowledge,
    management,
    memory,
    project,
    resource,
    search,
    prompt_router,
)
from basic_memory.api.v2.routers import knowledge_router as v2_knowledge, project_router as v2_project
from basic_memory.api.middleware import DeprecationMiddleware, DeprecationMetrics
from basic_memory.config import ConfigManager
from basic_memory.services.initialization import initialize_file_sync, initialize_app


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app. Not called in stdio mcp mode"""

    app_config = ConfigManager().config
    logger.info("Starting Basic Memory API")

    await initialize_app(app_config)

    # Cache database connections in app state for performance
    logger.info("Initializing database and caching connections...")
    engine, session_maker = await db.get_or_create_db(app_config.database_path)
    app.state.engine = engine
    app.state.session_maker = session_maker
    logger.info("Database connections cached in app state")

    logger.info(f"Sync changes enabled: {app_config.sync_changes}")
    if app_config.sync_changes:
        # start file sync task in background
        app.state.sync_task = asyncio.create_task(initialize_file_sync(app_config))
    else:
        logger.info("Sync changes disabled. Skipping file sync service.")

    # proceed with startup
    yield

    logger.info("Shutting down Basic Memory API")
    if app.state.sync_task:
        logger.info("Stopping sync...")
        app.state.sync_task.cancel()  # pyright: ignore

    await db.shutdown_db()


# Initialize FastAPI app
app = FastAPI(
    title="Basic Memory API",
    description="Knowledge graph API for basic-memory",
    version=version,
    lifespan=lifespan,
)

# Initialize deprecation metrics for tracking v1/v2 adoption
deprecation_metrics = DeprecationMetrics()
app.state.deprecation_metrics = deprecation_metrics

# Add deprecation middleware for v1 endpoints
# Sunset date: June 30, 2026 (6 months after v2 release)
app.add_middleware(
    DeprecationMiddleware,
    sunset_date="Tue, 30 Jun 2026 23:59:59 GMT",
    metrics=deprecation_metrics,
)


# Include v1 routers (deprecated)
app.include_router(knowledge.router, prefix="/{project}")
app.include_router(memory.router, prefix="/{project}")
app.include_router(resource.router, prefix="/{project}")
app.include_router(search.router, prefix="/{project}")
app.include_router(project.project_router, prefix="/{project}")
app.include_router(directory_router.router, prefix="/{project}")
app.include_router(prompt_router.router, prefix="/{project}")
app.include_router(importer_router.router, prefix="/{project}")

# Include v2 routers (current)
app.include_router(v2_knowledge, prefix="/v2/{project_id}")
app.include_router(v2_project, prefix="/v2")

# Project resource router works across projects
app.include_router(project.project_resource_router)
app.include_router(management.router)

# Auth routes are handled by FastMCP automatically when auth is enabled


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
