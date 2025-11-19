"""Management router for basic-memory API."""

import asyncio

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

from basic_memory.config import ConfigManager
from basic_memory.deps import SyncServiceDep, ProjectRepositoryDep

router = APIRouter(prefix="/management", tags=["management"])


class WatchStatusResponse(BaseModel):
    """Response model for watch status."""

    running: bool
    """Whether the watch service is currently running."""


@router.get("/watch/status", response_model=WatchStatusResponse)
async def get_watch_status(request: Request) -> WatchStatusResponse:
    """Get the current status of the watch service."""
    return WatchStatusResponse(
        running=request.app.state.watch_task is not None and not request.app.state.watch_task.done()
    )


@router.post("/watch/start", response_model=WatchStatusResponse)
async def start_watch_service(
    request: Request, project_repository: ProjectRepositoryDep, sync_service: SyncServiceDep
) -> WatchStatusResponse:
    """Start the watch service if it's not already running."""

    # needed because of circular imports from sync -> app
    from basic_memory.sync import WatchService
    from basic_memory.sync.background_sync import create_background_sync_task

    if request.app.state.watch_task is not None and not request.app.state.watch_task.done():
        # Watch service is already running
        return WatchStatusResponse(running=True)

    app_config = ConfigManager().config

    # Create and start a new watch service
    logger.info("Starting watch service via management API")

    # Get services needed for the watch task
    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
    )

    # Create and store the task
    watch_task = create_background_sync_task(sync_service, watch_service)
    request.app.state.watch_task = watch_task

    return WatchStatusResponse(running=True)


@router.post("/watch/stop", response_model=WatchStatusResponse)
async def stop_watch_service(request: Request) -> WatchStatusResponse:  # pragma: no cover
    """Stop the watch service if it's running."""
    if request.app.state.watch_task is None or request.app.state.watch_task.done():
        # Watch service is not running
        return WatchStatusResponse(running=False)

    # Cancel the running task
    logger.info("Stopping watch service via management API")
    request.app.state.watch_task.cancel()

    # Wait for it to be properly cancelled
    try:
        await request.app.state.watch_task
    except asyncio.CancelledError:
        pass

    request.app.state.watch_task = None
    return WatchStatusResponse(running=False)


@router.get("/deprecation-info")
async def get_deprecation_info() -> dict:
    """Get information about deprecated API versions.

    Returns deprecation timeline, migration guides, and sunset dates.
    This endpoint helps clients understand the API migration path from v1 to v2.
    """
    return {
        "v1": {
            "status": "deprecated",
            "sunset_date": "2026-06-30T23:59:59Z",
            "sunset_date_http": "Tue, 30 Jun 2026 23:59:59 GMT",
            "successor": "v2",
            "migration_guide": "docs/migration/v1-to-v2.md",
            "breaking_changes": [
                "Entity identifiers changed from paths to integer IDs",
                "URL structure changed from /{project}/endpoint to /v2/{project}/endpoint",
                "Memory URLs now support memory://id/{entity_id} format",
                "Direct ID lookups replace cascading identifier resolution",
            ],
            "affected_endpoints": [
                "/{project}/knowledge/entities/{identifier:path}",
                "/{project}/memory/{uri:path}",
                "/{project}/search/*",
                "/{project}/resource/*",
                "/{project}/directory/*",
            ],
        },
        "v2": {
            "status": "stable",
            "release_date": "2025-01-01T00:00:00Z",
            "base_url": "/v2/{project}",
            "documentation": "https://docs.basic-memory.io/api/v2",
            "key_features": [
                "ID-based entity references for improved performance",
                "Stable identifiers that don't change with file moves",
                "Better caching support",
                "Identifier resolution endpoint for migration compatibility",
            ],
        },
    }


@router.get("/metrics/deprecation")
async def get_deprecation_metrics(request: Request) -> dict:
    """Get v1 API deprecation metrics.

    Returns usage statistics for v1 and v2 endpoints to help monitor
    the migration progress.
    """
    return request.app.state.deprecation_metrics.get_stats()
