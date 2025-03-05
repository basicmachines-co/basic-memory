"""FastAPI application for basic-memory knowledge graph API."""

# Suppress logfire warnings
import os

os.environ["LOGFIRE_IGNORE_NO_CONFIG"] = "1"

from contextlib import asynccontextmanager

import logfire
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from loguru import logger

from basic_memory import db
from basic_memory.config import config as app_config
from basic_memory.api.routers import knowledge, search, memory, resource


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app."""
    await db.run_migrations(app_config)
    yield
    logger.info("Shutting down Basic Memory API")
    await db.shutdown_db()


# Initialize FastAPI app
app = FastAPI(
    title="Basic Memory API",
    description="Knowledge graph API for basic-memory",
    version="0.1.0",
    lifespan=lifespan,
)

if app_config != "test":
    logfire.instrument_fastapi(app)


# Include routers
app.include_router(knowledge.router)
app.include_router(search.router)
app.include_router(memory.router)
app.include_router(resource.router)


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
