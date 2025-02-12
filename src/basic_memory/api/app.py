"""FastAPI application for basic-memory knowledge graph API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from loguru import logger

from basic_memory import db
from basic_memory.api.routers import knowledge, search, memory, resource
from basic_memory.config import config
from basic_memory.services import DatabaseService


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app."""
    logger.info("Starting Basic Memory API")

    # check the db state
    await check_db(app)
    yield
    logger.info("Shutting down Basic Memory API")
    await db.shutdown_db()


async def check_db(app: FastAPI):  # pragma: no cover
    logger.info("Checking database state")

    # Initialize DB management service
    db_service = DatabaseService(
        config=config,
    )

    # Check and initialize DB if needed
    if not await db_service.check_db():
        raise RuntimeError("Database initialization failed")

    # Clean up old backups on shutdown
    await db_service.cleanup_backups()


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


@app.exception_handler(Exception)
async def exception_handler(request, exc):  # pragma: no cover
    logger.exception(
        f"An unhandled exception occurred for request '{request.url}', exception: {exc}"
    )
    return await http_exception_handler(request, HTTPException(status_code=500, detail=str(exc)))
