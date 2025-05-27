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
from basic_memory.config import app_config
from basic_memory.services.initialization import initialize_app, initialize_file_sync


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app."""
    # Initialize app and database
    logger.info("Starting Basic Memory API")
    await initialize_app(app_config)

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


# Include routers
app.include_router(knowledge.router, prefix="/{project}")
app.include_router(memory.router, prefix="/{project}")
app.include_router(resource.router, prefix="/{project}")
app.include_router(search.router, prefix="/{project}")
app.include_router(project.project_router, prefix="/{project}")
app.include_router(directory_router.router, prefix="/{project}")
app.include_router(prompt_router.router, prefix="/{project}")
app.include_router(importer_router.router, prefix="/{project}")

# Project resource router works accross projects
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
