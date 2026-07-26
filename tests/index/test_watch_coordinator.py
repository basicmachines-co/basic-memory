"""Regression tests for WatchCoordinator shutdown behavior."""

from __future__ import annotations

import asyncio

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.index.watch_coordinator import WatchCoordinator, WatchStatus


@pytest.mark.asyncio
async def test_start_waits_for_recovery_before_reporting_running(
    app_config: BasicMemoryConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API/MCP lifespan must not accept writes while startup recovery is active."""
    allow_recovery_to_finish = asyncio.Event()
    keep_watcher_running = asyncio.Event()

    async def initialize_after_recovery(
        config: BasicMemoryConfig,
        quiet: bool = True,
        *,
        recovery_complete: asyncio.Event | None = None,
    ) -> None:
        del config, quiet
        await allow_recovery_to_finish.wait()
        assert recovery_complete is not None
        recovery_complete.set()
        await keep_watcher_running.wait()

    monkeypatch.setattr(
        "basic_memory.services.initialization.initialize_file_indexing",
        initialize_after_recovery,
    )
    coordinator = WatchCoordinator(config=app_config)

    start_task = asyncio.create_task(coordinator.start())
    await asyncio.sleep(0)

    assert coordinator.status == WatchStatus.STARTING
    assert not start_task.done()

    allow_recovery_to_finish.set()
    await start_task

    assert coordinator.status == WatchStatus.RUNNING
    await coordinator.stop()


@pytest.mark.asyncio
async def test_start_propagates_failure_before_recovery_completes(
    app_config: BasicMemoryConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed recovery must unblock startup and preserve the error state."""

    async def fail_before_recovery(
        config: BasicMemoryConfig,
        quiet: bool = True,
        *,
        recovery_complete: asyncio.Event | None = None,
    ) -> None:
        del config, quiet, recovery_complete
        raise RuntimeError("recovery boom")

    monkeypatch.setattr(
        "basic_memory.services.initialization.initialize_file_indexing",
        fail_before_recovery,
    )
    coordinator = WatchCoordinator(config=app_config)

    with pytest.raises(RuntimeError, match="recovery boom"):
        await coordinator.start()

    assert coordinator.status == WatchStatus.ERROR


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
