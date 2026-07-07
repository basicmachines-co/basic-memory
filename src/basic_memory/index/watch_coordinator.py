"""Lifecycle coordinator for local event-index watching."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto

from loguru import logger

from basic_memory.config import BasicMemoryConfig


class WatchStatus(Enum):
    """Status of the local watch coordinator."""

    NOT_STARTED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()


@dataclass
class WatchCoordinator:
    """Coordinate local event-index watcher lifecycle across entrypoints."""

    config: BasicMemoryConfig
    should_watch: bool = True
    skip_reason: str | None = None
    quiet: bool = True

    _status: WatchStatus = field(default=WatchStatus.NOT_STARTED, init=False)
    _watch_task: asyncio.Task[None] | None = field(default=None, init=False)

    @property
    def status(self) -> WatchStatus:
        """Current coordinator status."""
        return self._status

    @property
    def is_running(self) -> bool:
        """Return whether local watching is running."""
        return self._status == WatchStatus.RUNNING

    async def start(self) -> None:
        """Start local event-index watching in the background when enabled."""
        if not self.should_watch:
            if self.skip_reason:
                logger.debug(f"{self.skip_reason} - skipping local file watch")
            self._status = WatchStatus.STOPPED
            return

        if self._status in (WatchStatus.RUNNING, WatchStatus.STARTING):
            logger.warning("Watch coordinator already running or starting")
            return

        self._status = WatchStatus.STARTING
        logger.info("Starting local event-index watcher in background")

        try:
            # Imported here to avoid a circular import: services.initialization
            # depends on basic_memory.index, whose __init__ imports this module.
            from basic_memory.services.initialization import initialize_file_indexing

            async def _watch_runner() -> None:  # pragma: no cover
                try:
                    await initialize_file_indexing(self.config, quiet=self.quiet)
                except asyncio.CancelledError:
                    logger.debug("Local event-index watcher cancelled")
                    raise
                except Exception as exc:
                    logger.error(f"Error in local event-index watcher: {exc}")
                    self._status = WatchStatus.ERROR
                    raise

            self._watch_task = asyncio.create_task(_watch_runner())
            self._status = WatchStatus.RUNNING
            logger.info("Watch coordinator started successfully")

        except Exception as exc:  # pragma: no cover
            logger.error(f"Failed to start watch coordinator: {exc}")
            self._status = WatchStatus.ERROR
            raise

    async def stop(self) -> None:
        """Cancel and await the background watcher task."""
        if self._status in (WatchStatus.NOT_STARTED, WatchStatus.STOPPED):
            return

        if self._watch_task is None:  # pragma: no cover
            self._status = WatchStatus.STOPPED
            return

        self._status = WatchStatus.STOPPING
        logger.info("Stopping watch coordinator...")

        self._watch_task.cancel()
        try:
            await self._watch_task
        except asyncio.CancelledError:
            logger.info("Local event-index watcher task cancelled successfully")
        except Exception as exc:
            # Trigger: the background watcher already died and stored its crash on the task.
            # Why: awaiting an already-failed task re-raises the stored exception here;
            #      propagating it would abort the rest of shutdown (e.g. draining
            #      202-materialization) before cleanup completes.
            # Outcome: log the prior crash and continue clean shutdown.
            logger.warning(f"Local event-index watcher had crashed before shutdown: {exc}")

        self._watch_task = None
        self._status = WatchStatus.STOPPED
        logger.info("Watch coordinator stopped")

    def get_status_info(self) -> dict[str, object]:
        """Return diagnostic coordinator state."""
        return {
            "status": self._status.name,
            "should_watch": self.should_watch,
            "skip_reason": self.skip_reason,
            "has_task": self._watch_task is not None,
        }
