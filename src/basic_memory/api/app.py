"""FastAPI application for basic-memory knowledge graph API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from loguru import logger

from basic_memory import db
from basic_memory.api.routers import knowledge, memory, project_info, resource, search
from basic_memory.config import config as project_config
from basic_memory.services.initialization import initialize_app


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app."""
    # Initialize database and file sync services
    watch_task = await initialize_app(project_config)

    # proceed with startup
    yield

    logger.info("Shutting down Basic Memory API")
    if watch_task:
        watch_task.cancel()

    await db.shutdown_db()


# Initialize FastAPI app
app = FastAPI(
    title="Basic Memory API",
    description="Knowledge graph API for basic-memory",
    version="0.1.0",
    lifespan=lifespan,
)


# Include routers
app.include_router(knowledge.router)
app.include_router(search.router)
app.include_router(memory.router)
app.include_router(resource.router)
app.include_router(project_info.router)


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
