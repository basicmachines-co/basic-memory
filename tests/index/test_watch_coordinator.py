"""Regression tests for WatchCoordinator shutdown behavior."""

from __future__ import annotations

import asyncio

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.index.watch_coordinator import WatchCoordinator, WatchStatus


@pytest.mark.asyncio
async def test_stop_does_not_propagate_prior_watcher_crash(
    app_config: BasicMemoryConfig,
) -> None:
    """stop() must swallow a previously-stored watcher crash and shut down cleanly.

    A crashed background task re-raises its stored exception on every await; if
    stop() let that escape, the rest of shutdown (e.g. draining 202-materialization)
    would be aborted before cleanup ran.
    """
    coordinator = WatchCoordinator(config=app_config)

    async def crashing_watcher() -> None:
        raise RuntimeError("watcher boom")

    task = asyncio.create_task(crashing_watcher())
    # Let the task run to completion so its exception is stored on the task.
    while not task.done():
        await asyncio.sleep(0)

    coordinator._watch_task = task
    coordinator._status = WatchStatus.RUNNING

    # Must not raise even though awaiting the done task re-raises RuntimeError.
    await coordinator.stop()

    assert coordinator.status == WatchStatus.STOPPED
    assert coordinator._watch_task is None
