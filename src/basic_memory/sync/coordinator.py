"""SyncCoordinator - centralized sync/watch lifecycle management.

This module provides a single coordinator that manages the lifecycle of
file synchronization and watch services across all entry points (API, MCP, CLI).

The coordinator handles:
- Starting/stopping watch service
- Scheduling background sync
- Reporting status
- Clean shutdown behavior
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from loguru import logger

from basic_memory.config import BasicMemoryConfig


class SyncStatus(Enum):
    """Status of the sync coordinator."""

    NOT_STARTED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()


@dataclass
class SyncCoordinator:
    """Centralized coordinator for sync/watch lifecycle.

    Manages the lifecycle of file synchronization services, providing:
    - Unified start/stop interface
    - Status tracking
    - Clean shutdown with proper task cancellation

    Args:
        config: BasicMemoryConfig with sync settings
        should_sync: Whether sync should be enabled (from container decision)
        skip_reason: Human-readable reason if sync is skipped

    Usage:
        coordinator = SyncCoordinator(config=config, should_sync=True)
        await coordinator.start()
        # ... application runs ...
        await coordinator.stop()
    """

    config: BasicMemoryConfig
    should_sync: bool = True
    skip_reason: Optional[str] = None

    # Internal state (not constructor args)
    _status: SyncStatus = field(default=SyncStatus.NOT_STARTED, init=False)
    _sync_task: Optional[asyncio.Task] = field(default=None, init=False)

    @property
    def status(self) -> SyncStatus:
        """Current status of the coordinator."""
        return self._status

    @property
    def is_running(self) -> bool:
        """Whether sync is currently running."""
        return self._status == SyncStatus.RUNNING

    async def start(self) -> None:
        """Start the sync/watch service if enabled.

        This is a non-blocking call that starts the sync task in the background.
        Use stop() to cleanly shut down.
        """
        if not self.should_sync:
            if self.skip_reason:
                logger.info(f"{self.skip_reason} - skipping local file sync")
            self._status = SyncStatus.STOPPED
            return

        if self._status in (SyncStatus.RUNNING, SyncStatus.STARTING):
            logger.warning("Sync coordinator already running or starting")
            return

        self._status = SyncStatus.STARTING
        logger.info("Starting file sync in background")

        try:
            # Deferred import to avoid circular dependency
            from basic_memory.services.initialization import initialize_file_sync

            async def _file_sync_runner() -> None:  # pragma: no cover
                """Run the file sync service."""
                try:
                    await initialize_file_sync(self.config)
                except asyncio.CancelledError:
                    logger.debug("File sync cancelled")
                    raise
                except Exception as e:
                    logger.error(f"Error in file sync: {e}")
                    self._status = SyncStatus.ERROR
                    raise

            self._sync_task = asyncio.create_task(_file_sync_runner())
            self._status = SyncStatus.RUNNING
            logger.info("Sync coordinator started successfully")

        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to start sync coordinator: {e}")
            self._status = SyncStatus.ERROR
            raise

    async def stop(self) -> None:
        """Stop the sync/watch service cleanly.

        Cancels the background task and waits for it to complete.
        Safe to call even if not running.
        """
        if self._status in (SyncStatus.NOT_STARTED, SyncStatus.STOPPED):
            return

        if self._sync_task is None:  # pragma: no cover
            self._status = SyncStatus.STOPPED
            return

        self._status = SyncStatus.STOPPING
        logger.info("Stopping sync coordinator...")

        self._sync_task.cancel()
        try:
            await self._sync_task
        except asyncio.CancelledError:
            logger.info("File sync task cancelled successfully")

        self._sync_task = None
        self._status = SyncStatus.STOPPED
        logger.info("Sync coordinator stopped")

    def get_status_info(self) -> dict:
        """Get status information for reporting.

        Returns:
            Dictionary with status details for diagnostics
        """
        return {
            "status": self._status.name,
            "should_sync": self.should_sync,
            "skip_reason": self.skip_reason,
            "has_task": self._sync_task is not None,
        }


__all__ = [
    "SyncCoordinator",
    "SyncStatus",
]
