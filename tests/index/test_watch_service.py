"""Regression tests for local watcher batch isolation and config re-reads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from basic_memory.config import BasicMemoryConfig, ConfigManager, ProjectEntry
from basic_memory.index.watch_service import WatchService
from basic_memory.models import Project


@pytest.mark.asyncio
async def test_handle_changes_isolated_contains_one_project_failure(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
) -> None:
    """One project's handler failure must not abort or drop other projects' batches."""
    handled: list[str] = []

    class FailingWatchService(WatchService):
        async def handle_changes(self, project, changes) -> None:  # type: ignore[override]
            handled.append(project.name)
            if project.name == "boom":
                raise RuntimeError("indexing boom")

    watch_service = FailingWatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )

    boom = SimpleNamespace(name="boom")
    healthy = SimpleNamespace(name="healthy")

    # Mirror _watch_projects_cycle: gather isolated handlers for every project batch.
    await asyncio.gather(
        watch_service._handle_changes_isolated(cast(Project, boom), set()),
        watch_service._handle_changes_isolated(cast(Project, healthy), set()),
    )

    # The failing project did not prevent the healthy project from being handled,
    # and the error was recorded rather than propagated out of gather().
    assert set(handled) == {"boom", "healthy"}
    assert watch_service.state.error_count == 1
    assert watch_service.state.recent_events[0].status == "error"


@pytest.mark.asyncio
async def test_project_is_configured_rereads_current_config(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    config_home,
    config_manager,
) -> None:
    """A project deleted from config after startup must not be treated as configured."""
    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )

    # Startup snapshot still lists the project, and it is currently on disk.
    assert test_project.name in watch_service.app_config.projects
    assert watch_service._project_is_configured(test_project) is True

    # Simulate `bm project remove` rewriting config without the watched project
    # after the watcher started (its app_config snapshot is now stale).
    remaining_config = app_config.model_copy(
        update={
            "projects": {"other-project": ProjectEntry(path=str(config_home))},
            "default_project": "other-project",
        }
    )
    config_manager.save_config(remaining_config)

    # Snapshot is stale, but the guard re-reads current config and drops the project.
    assert test_project.name in watch_service.app_config.projects
    assert ConfigManager().config.projects.keys() == {"other-project"}
    assert watch_service._project_is_configured(test_project) is False
