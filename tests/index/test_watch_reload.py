"""A project added or moved in this process wakes the watcher's reload (#1975)."""

from __future__ import annotations

import asyncio

import pytest

from basic_memory import db
from basic_memory.config import ConfigManager, ProjectEntry
from basic_memory.index.watch_reload import (
    request_watch_reload,
    wait_for_reload,
    watch_reload_requests,
)
from basic_memory.index.watch_service import WatchService
from basic_memory.services.project_service import ProjectService


@pytest.mark.asyncio
async def test_a_request_with_no_watcher_does_nothing() -> None:
    request_watch_reload()

    with watch_reload_requests() as reload_requested:
        assert not reload_requested.is_set()


@pytest.mark.asyncio
async def test_a_request_ends_the_wait_and_is_consumed() -> None:
    with watch_reload_requests() as reload_requested:
        request_watch_reload()
        await asyncio.wait_for(wait_for_reload(reload_requested, timeout=300), timeout=2)

        assert not reload_requested.is_set()


@pytest.mark.asyncio
async def test_new_project_restarts_the_watcher_before_the_interval(
    app_config,
    config_manager: ConfigManager,
    project_repository,
    session_maker,
    test_project,
    tmp_path,
):
    # The default interval is 300s; a reload request must not wait for it.
    config = app_config.model_copy(update={"watch_project_reload_interval": 300})
    watcher = WatchService(config, project_repository, session_maker)
    stop_event = asyncio.Event()
    timer = asyncio.create_task(watcher._schedule_restart(stop_event, [test_project]))
    try:
        root = tmp_path / "added-now"
        root.mkdir()
        async with db.scoped_session(session_maker) as session:
            await project_repository.create(
                session, {"name": "added-now", "path": str(root), "is_active": True}
            )
        updated = config_manager.config.model_copy(deep=True)
        updated.projects["added-now"] = ProjectEntry(path=str(root))
        config_manager.save_config(updated)

        request_watch_reload()

        await asyncio.wait_for(stop_event.wait(), timeout=5)
    finally:
        timer.cancel()
        await asyncio.wait([timer])


@pytest.mark.asyncio
async def test_adding_and_moving_a_project_request_a_reload(
    project_service: ProjectService, tmp_path_factory: pytest.TempPathFactory
):
    # Outside tmp_path, which is the test project's own root.
    root = tmp_path_factory.mktemp("reload-roots")
    with watch_reload_requests() as reload_requested:
        await project_service.add_project("reload-me", str(root / "reload-me"))
        assert reload_requested.is_set()
        reload_requested.clear()

        await project_service.move_project("reload-me", str(root / "moved"))
        assert reload_requested.is_set()
