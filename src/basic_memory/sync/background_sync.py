import asyncio

from loguru import logger

from basic_memory.config import config as project_config
from basic_memory.sync import SyncService, WatchService


async def sync_and_watch(
    sync_service: SyncService, watch_service: WatchService
):  # pragma: no cover
    """Run sync and watch service."""

    logger.info(f"Starting watch service to sync file changes in dir: {project_config.home}")
    # full sync
    await sync_service.sync(project_config.home)

    # watch changes
    await watch_service.run()


async def create_background_sync_task(
    sync_service: SyncService, watch_service: WatchService
):  # pragma: no cover
    return asyncio.create_task(sync_and_watch(sync_service, watch_service))
