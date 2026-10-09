"""Regression tests for local watcher batch isolation and config re-reads."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import override, cast

import pytest
from loguru import logger
from watchfiles import Change

from basic_memory.config import BasicMemoryConfig, ConfigManager, ProjectEntry
from basic_memory.index.watch_service import WatchService, warn_unreadable_new_directories
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
        @override
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


@pytest.mark.asyncio
async def test_select_projects_to_watch_matches_constrained_permalink_case_insensitively(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
) -> None:
    """A mixed-case MCP project constraint must select its normalized project."""
    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        constrained_project="Test Project",
    )

    projects = await watch_service._select_projects_to_watch()

    assert [project.id for project in projects] == [test_project.id]


def _capture_warnings() -> tuple[list[str], int]:
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message).strip()),
        format="{message}",
        level="WARNING",
    )
    return messages, sink_id


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="root can read a directory with mode 000",
)
@pytest.mark.asyncio
async def test_handle_changes_warns_when_a_new_directory_cannot_be_read(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project: Project,
    project_config,
) -> None:
    """A new directory the watcher cannot read is named, with the command that recovers it."""
    closed = Path(project_config.home) / "people"
    closed.mkdir(mode=0o000)
    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )

    messages, sink_id = _capture_warnings()
    try:
        await watch_service.handle_changes(test_project, {(Change.added, str(closed))})
    finally:
        logger.remove(sink_id)
        closed.chmod(0o755)

    warnings = [message for message in messages if "New directory cannot be read" in message]
    assert len(warnings) == 1
    assert str(closed) in warnings[0]
    assert f"bm project index {test_project.name}" in warnings[0]


def test_warn_unreadable_new_directories_ignores_readable_directories_and_files(
    tmp_path: Path,
) -> None:
    """Readable directories, files, and non-add changes produce no warning."""
    readable = tmp_path / "readable"
    readable.mkdir()
    note = tmp_path / "note.md"
    note.write_text("# Note\n")
    gone = tmp_path / "gone"
    project = cast(Project, SimpleNamespace(name="test-project"))

    messages, sink_id = _capture_warnings()
    try:
        warn_unreadable_new_directories(
            project,
            local_index_available=True,
            changes={
                (Change.added, str(readable)),
                (Change.added, str(note)),
                (Change.added, str(gone)),
                (Change.modified, str(readable)),
            },
        )
    finally:
        logger.remove(sink_id)

    assert messages == []


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="root can read a directory with mode 000",
)
def test_unreadable_directory_warning_quotes_a_project_name_with_spaces(tmp_path: Path) -> None:
    """The recovery command must survive copy-paste for a project named with a space."""
    closed = tmp_path / "people"
    closed.mkdir(mode=0o000)
    project = cast(Project, SimpleNamespace(name="My Notes"))

    messages, sink_id = _capture_warnings()
    try:
        warn_unreadable_new_directories(
            project, {(Change.added, str(closed))}, local_index_available=True
        )
    finally:
        logger.remove(sink_id)
        closed.chmod(0o755)

    assert len(messages) == 1
    assert "`bm project index 'My Notes'`" in messages[0]


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="root can read a directory with mode 000",
)
@pytest.mark.parametrize("local_index_available", [True, False])
def test_unreadable_directory_warning_survives_braces_and_fits_the_mode(
    tmp_path: Path, local_index_available: bool
) -> None:
    """Braces in a path are not format fields, and a cloud project gets no local reindex hint."""
    closed = tmp_path / "{foo}"
    closed.mkdir(mode=0o000)
    project = cast(Project, SimpleNamespace(name="research"))

    messages, sink_id = _capture_warnings()
    try:
        warn_unreadable_new_directories(
            project, {(Change.added, str(closed))}, local_index_available=local_index_available
        )
    finally:
        logger.remove(sink_id)
        closed.chmod(0o755)

    assert len(messages) == 1
    assert str(closed) in messages[0]
    assert ("bm project index research" in messages[0]) is local_index_available
