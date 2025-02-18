"""FastAPI application for basic-memory knowledge graph API."""

from contextlib import asynccontextmanager

import logfire
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from loguru import logger

import basic_memory
from basic_memory import db
from basic_memory.config import config as app_config
from basic_memory.api.routers import knowledge, search, memory, resource
from basic_memory.utils import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app."""
    setup_logging(log_file=".basic-memory/basic-memory.log")
    logger.info(f"Starting Basic Memory API {basic_memory.__version__}")
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
        f"An unhandled exception occurred for request '{request.url}', exception: {exc}"
    )
    return await http_exception_handler(request, HTTPException(status_code=500, detail=str(exc)))
