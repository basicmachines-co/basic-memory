"""FastAPI application for basic-memory knowledge graph API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from loguru import logger

from basic_memory import db
from basic_memory.config import config as app_config
from basic_memory.api.routers import knowledge, search, memory, resource
from alembic import command
from alembic.config import Config

from basic_memory.db import DatabaseType
from basic_memory.repository.search_repository import SearchRepository


async def run_migrations():  # pragma: no cover
    """Run any pending alembic migrations."""
    logger.info("Running database migrations...")
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        logger.info("Migrations completed successfully")

        _, session_maker = await db.get_or_create_db(
            app_config.database_path, DatabaseType.FILESYSTEM
        )
        await SearchRepository(session_maker).init_search_index()
    except Exception as e:
        logger.error(f"Error running migrations: {e}")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app."""
    logger.info("Starting Basic Memory API")
    await run_migrations()
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
