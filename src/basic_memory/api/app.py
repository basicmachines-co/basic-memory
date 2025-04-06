"""FastAPI application for basic-memory knowledge graph API."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from loguru import logger

from basic_memory import db
from basic_memory.api.routers import knowledge, memory, project_info, resource, search
from basic_memory.config import config as project_config
from basic_memory.config import config_manager
from basic_memory.sync import SyncService, WatchService


async def run_background_sync(sync_service: SyncService, watch_service: WatchService): # pragma: no cover
    logger.info(f"Starting watch service to sync file changes in dir: {project_config.home}")
    # full sync
    await sync_service.sync(project_config.home, show_progress=False)

    # watch changes
    await watch_service.run()


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover
    """Lifecycle manager for the FastAPI app."""
    await db.run_migrations(project_config)

    # app config
    basic_memory_config = config_manager.load_config()
    logger.info(f"Sync changes enabled: {basic_memory_config.sync_changes}")
    logger.info(f"Update permalinks on move enabled: {basic_memory_config.update_permalinks_on_move}")

    watch_task = None
    if basic_memory_config.sync_changes:
        # import after migrations have run
        from basic_memory.cli.commands.sync import get_sync_service

        sync_service = await get_sync_service()
        watch_service = WatchService(
            sync_service=sync_service,
            file_service=sync_service.entity_service.file_service,
            config=project_config,
        )
        watch_task = asyncio.create_task(run_background_sync(sync_service, watch_service))
    else:
        logger.info("Sync changes disabled. Skipping watch service.")


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
