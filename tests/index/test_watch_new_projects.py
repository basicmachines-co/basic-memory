"""Files written before a new project's watch is attached must be indexed (#1817)."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import ConfigManager, ProjectEntry
from basic_memory.index.local_project import LocalProjectIndexRuntimeFactory
from basic_memory.index import watch_service as watch_module
from basic_memory.index.watch_service import WatchService
from basic_memory.models import Project
from basic_memory.repository import ProjectRepository
from basic_memory.repository.entity_repository import EntityRepository


async def _register_project(
    name: str,
    root: Path,
    config_manager: ConfigManager,
    project_repository: ProjectRepository,
    session_maker: async_sessionmaker[AsyncSession],
) -> Project:
    root.mkdir(parents=True, exist_ok=True)
    async with db.scoped_session(session_maker) as session:
        project = await project_repository.create(
            session,
            {"name": name, "path": str(root), "is_active": True, "is_default": False},
        )
    config = config_manager.config.model_copy(deep=True)
    config.projects[name] = ProjectEntry(path=str(root))
    config_manager.save_config(config)
    return project


@pytest.mark.asyncio
async def test_watch_selection_rereads_added_and_removed_project_config(
    app_config, config_manager, project_repository, session_maker, test_project, tmp_path_factory
):
    watcher = WatchService(app_config.model_copy(deep=True), project_repository, session_maker)
    assert [project.id for project in await watcher._select_projects_to_watch()] == [
        test_project.id
    ]

    added = await _register_project(
        "added-project",
        tmp_path_factory.mktemp("added-watch-root"),
        config_manager,
        project_repository,
        session_maker,
    )
    selected = await watcher._select_projects_to_watch()
    assert {project.id for project in selected} == {test_project.id, added.id}

    config = config_manager.config.model_copy(deep=True)
    del config.projects[test_project.name]
    config.default_project = added.name
    config_manager.save_config(config)
    assert [project.id for project in await watcher._select_projects_to_watch()] == [added.id]


@pytest.mark.asyncio
async def test_new_project_backfill_runs_after_attach_and_only_once(
    app_config,
    config_manager,
    project_repository,
    session_maker,
    test_project,
    tmp_path_factory,
    monkeypatch,
):
    watcher = WatchService(app_config.model_copy(deep=True), project_repository, session_maker)
    root = tmp_path_factory.mktemp("new-watch-project")
    attached = False
    timeout_yields: list[bool] = []

    async def ready_watch(*paths, stop_event, yield_on_timeout=False, **_kwargs):
        nonlocal attached
        attached = True
        if str(root) in paths:
            # A pre-attach scan would miss this file; no file-change event follows.
            (root / "during-attach.md").write_text("# During Attach\n", encoding="utf-8")
            timeout_yields.append(yield_on_timeout)
        yield set()
        stop_event.set()

    monkeypatch.setattr(watch_module, "awatch", ready_watch)
    await watcher._watch_projects_cycle([test_project], asyncio.Event())
    added = await _register_project(
        "new-project", root, config_manager, project_repository, session_maker
    )
    (root / "before-attach.md").write_text("# Before Attach\n- [goal] find me\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "skip.md").write_text("# Ignore Me\n", encoding="utf-8")
    (root / ".hidden.md").write_text("# Hidden\n", encoding="utf-8")
    indexed: list[int] = []
    original_runtime = LocalProjectIndexRuntimeFactory.runtime_for_project

    async def record_runtime(factory, project):
        assert attached, "the project must be watched before its one-off scan"
        indexed.append(project.id)
        return await original_runtime(factory, project)

    monkeypatch.setattr(LocalProjectIndexRuntimeFactory, "runtime_for_project", record_runtime)
    attached = False
    await watcher._watch_projects_cycle([test_project, added], asyncio.Event())

    repository = EntityRepository(added.id)
    async with db.scoped_session(session_maker) as session:
        assert await repository.get_by_file_path(session, "before-attach.md") is not None
        assert await repository.get_by_file_path(session, "during-attach.md") is not None
        assert await repository.get_by_file_path(session, "ignored/skip.md") is None
        assert await repository.get_by_file_path(session, ".hidden.md") is None
    await watcher._watch_projects_cycle([test_project, added], asyncio.Event())
    assert indexed == [added.id]
    assert timeout_yields[0] is True


@pytest.mark.asyncio
async def test_startup_projects_do_not_get_a_second_scan(
    app_config, config_manager, project_repository, session_maker, test_project, monkeypatch
):
    watcher = WatchService(
        app_config, project_repository, session_maker, initial_projects=[test_project]
    )

    async def ready_watch(*_paths, stop_event, **_kwargs):
        yield set()
        stop_event.set()

    async def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("startup already owns the initial project scan")

    monkeypatch.setattr(watch_module, "awatch", ready_watch)
    monkeypatch.setattr(watch_module, "run_local_project_index_for_project", unexpected_scan)
    await watcher._watch_projects_cycle([test_project], asyncio.Event())
    assert watcher.state.error_count == 0


@pytest.mark.asyncio
async def test_failed_backfill_is_isolated_and_retried_on_the_next_cycle(
    app_config,
    config_manager,
    project_repository,
    session_maker,
    test_project,
    tmp_path_factory,
    monkeypatch,
):
    watcher = WatchService(
        app_config, project_repository, session_maker, initial_projects=[test_project]
    )
    broken = await _register_project(
        "broken",
        tmp_path_factory.mktemp("broken-root"),
        config_manager,
        project_repository,
        session_maker,
    )
    healthy = await _register_project(
        "healthy",
        tmp_path_factory.mktemp("healthy-root"),
        config_manager,
        project_repository,
        session_maker,
    )
    for project in (broken, healthy):
        (Path(project.path) / "note.md").write_text("# Note\n", encoding="utf-8")
    attempts: list[int] = []
    fail = True
    original_index = watch_module.run_local_project_index_for_project

    async def index(project, **kwargs):
        attempts.append(project.id)
        if fail and project.id == broken.id:
            raise RuntimeError("scan failed")
        return await original_index(project, **kwargs)

    async def ready_watch(*_paths, stop_event, **_kwargs):
        yield set()
        stop_event.set()

    monkeypatch.setattr(watch_module, "awatch", ready_watch)
    monkeypatch.setattr(watch_module, "run_local_project_index_for_project", index)
    projects = [test_project, broken, healthy]
    await watcher._watch_projects_cycle(projects, asyncio.Event())
    assert watcher.state.error_count == 1
    async with db.scoped_session(session_maker) as session:
        assert await EntityRepository(healthy.id).get_by_file_path(session, "note.md") is not None
    fail = False
    await watcher._watch_projects_cycle(projects, asyncio.Event())
    assert Counter(attempts) == {broken.id: 2, healthy.id: 1}
    async with db.scoped_session(session_maker) as session:
        assert await EntityRepository(broken.id).get_by_file_path(session, "note.md") is not None


@pytest.mark.asyncio
async def test_project_removed_during_attach_is_not_backfilled(
    app_config,
    config_manager,
    project_repository,
    session_maker,
    test_project,
    tmp_path_factory,
    monkeypatch,
):
    added = await _register_project(
        "removed",
        tmp_path_factory.mktemp("removed-root"),
        config_manager,
        project_repository,
        session_maker,
    )
    watcher = WatchService(
        app_config, project_repository, session_maker, initial_projects=[test_project]
    )
    (Path(added.path) / "note.md").write_text("# Must Not Index\n", encoding="utf-8")

    async def ready_watch(*_paths, stop_event, **_kwargs):
        config = config_manager.config.model_copy(deep=True)
        del config.projects[added.name]
        config_manager.save_config(config)
        yield set()
        stop_event.set()

    monkeypatch.setattr(watch_module, "awatch", ready_watch)
    await watcher._watch_projects_cycle([test_project, added], asyncio.Event())
    async with db.scoped_session(session_maker) as session:
        assert await EntityRepository(added.id).get_by_file_path(session, "note.md") is None
    assert watcher.state.error_count == 0


@pytest.mark.asyncio
async def test_a_restart_during_backfill_keeps_the_project_pending(
    app_config,
    config_manager,
    project_repository,
    session_maker,
    test_project,
    tmp_path_factory,
    monkeypatch,
):
    added = await _register_project(
        "late",
        tmp_path_factory.mktemp("late-root"),
        config_manager,
        project_repository,
        session_maker,
    )
    watcher = WatchService(
        app_config, project_repository, session_maker, initial_projects=[test_project]
    )
    stop_event = asyncio.Event()
    original_index = watch_module.run_local_project_index_for_project
    attempts = 0

    async def index(project, **kwargs):
        nonlocal attempts
        attempts += 1
        result = await original_index(project, **kwargs)
        if attempts == 1:
            (Path(project.path) / "late.md").write_text("# Late Arrival\n", encoding="utf-8")
            stop_event.set()
        return result

    async def ready_watch(*_paths, **_kwargs):
        yield set()

    monkeypatch.setattr(watch_module, "awatch", ready_watch)
    monkeypatch.setattr(watch_module, "run_local_project_index_for_project", index)
    await watcher._watch_projects_cycle([test_project, added], stop_event)
    await watcher._watch_projects_cycle([test_project, added], asyncio.Event())
    async with db.scoped_session(session_maker) as session:
        assert await EntityRepository(added.id).get_by_file_path(session, "late.md") is not None
    assert attempts == 2


@pytest.mark.asyncio
async def test_real_quiet_watcher_backfills_then_handles_live_writes(
    app_config,
    config_manager,
    project_repository,
    session_maker,
    test_project,
    tmp_path_factory,
    monkeypatch,
):
    root = tmp_path_factory.mktemp("native-watch-root")
    added = await _register_project(
        "native", root, config_manager, project_repository, session_maker
    )
    (root / "before.md").write_text("# Before\n", encoding="utf-8")
    watcher = WatchService(
        app_config, project_repository, session_maker, initial_projects=[test_project]
    )
    scan_done = asyncio.Event()
    live_done = asyncio.Event()
    stop_event = asyncio.Event()
    original_index = watch_module.run_local_project_index_for_project
    original_handle = watcher.handle_changes

    async def index(project, **kwargs):
        result = await original_index(project, **kwargs)
        scan_done.set()
        return result

    async def handle(project, changes):
        await original_handle(project, changes)
        if any(Path(path).name == "live.md" for _change, path in changes):
            live_done.set()

    monkeypatch.setattr(watch_module, "run_local_project_index_for_project", index)
    monkeypatch.setattr(watcher, "handle_changes", handle)
    task = asyncio.create_task(watcher._watch_projects_cycle([added], stop_event))
    try:
        await asyncio.wait_for(scan_done.wait(), timeout=15)
        async with db.scoped_session(session_maker) as session:
            assert (
                await EntityRepository(added.id).get_by_file_path(session, "before.md") is not None
            )
        (root / "live.md").write_text("# Live\n", encoding="utf-8")
        await asyncio.wait_for(live_done.wait(), timeout=15)
        async with db.scoped_session(session_maker) as session:
            assert await EntityRepository(added.id).get_by_file_path(session, "live.md") is not None
    finally:
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_cancelling_backfill_closes_the_watcher_and_allows_retry(
    app_config,
    config_manager,
    project_repository,
    session_maker,
    test_project,
    tmp_path_factory,
    monkeypatch,
):
    added = await _register_project(
        "cancelled",
        tmp_path_factory.mktemp("cancelled-root"),
        config_manager,
        project_repository,
        session_maker,
    )
    watcher = WatchService(
        app_config, project_repository, session_maker, initial_projects=[test_project]
    )
    scan_started = asyncio.Event()
    closed = asyncio.Event()

    async def blocked_index(*_args, **_kwargs):
        scan_started.set()
        await asyncio.Event().wait()

    async def ready_watch(*_paths, **_kwargs):
        try:
            yield set()
        finally:
            closed.set()

    monkeypatch.setattr(watch_module, "awatch", ready_watch)
    with monkeypatch.context() as blocked:
        blocked.setattr(watch_module, "run_local_project_index_for_project", blocked_index)
        task = asyncio.create_task(watcher._watch_projects_cycle([added], asyncio.Event()))
        try:
            await asyncio.wait_for(scan_started.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert closed.is_set()
    assert watcher._sorted_watch_filter_roots is None
    assert watcher.state.error_count == 0

    (Path(added.path) / "retry.md").write_text("# Retry\n", encoding="utf-8")
    await watcher._watch_projects_cycle([added], asyncio.Event())
    async with db.scoped_session(session_maker) as session:
        assert await EntityRepository(added.id).get_by_file_path(session, "retry.md") is not None


@pytest.mark.asyncio
async def test_changing_a_project_root_does_not_run_a_full_scan(
    app_config,
    config_manager,
    project_repository,
    session_maker,
    test_project,
    tmp_path_factory,
    monkeypatch,
):
    """`bm project move` rewrites the path before the user moves the files.

    Rescanning the new, still-empty root would plan every old path as deleted and
    remove the project's index. Only a project identity the watcher has never seen
    needs the catch-up pass.
    """
    watcher = WatchService(
        app_config, project_repository, session_maker, initial_projects=[test_project]
    )
    root = tmp_path_factory.mktemp("changed-project-root")
    (root / "relocated.md").write_text("# Relocated\n", encoding="utf-8")
    async with db.scoped_session(session_maker) as session:
        moved = await project_repository.update(session, test_project.id, {"path": str(root)})
    assert moved is not None
    config = config_manager.config.model_copy(deep=True)
    config.projects[test_project.name] = ProjectEntry(path=str(root))
    config_manager.save_config(config)

    async def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("a path change must not rescan the new root")

    async def ready_watch(*_paths, **_kwargs):
        yield set()

    monkeypatch.setattr(watch_module, "run_local_project_index_for_project", unexpected_scan)
    monkeypatch.setattr(watch_module, "awatch", ready_watch)
    await watcher._watch_projects_cycle([moved], asyncio.Event())
    assert watcher.state.error_count == 0


@pytest.mark.asyncio
async def test_reload_waits_for_catch_up_and_buffered_changes(
    app_config, project_repository, session_maker
):
    """A reload must not stop the watcher while the catch-up scan blocks the consumer.

    watchfiles clears its pending batches on stop, so the timer waits for the
    catch-up drain instead of closing the cycle underneath it.
    """
    config = app_config.model_copy(update={"watch_project_reload_interval": 1})
    watcher = WatchService(config, project_repository, session_maker)
    stop_event = asyncio.Event()
    watcher._catch_up_in_progress = True

    timer = asyncio.create_task(watcher._schedule_restart(stop_event))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=1.2)
        watcher._catch_up_in_progress = False
        await asyncio.wait_for(stop_event.wait(), timeout=2)
    finally:
        timer.cancel()
        try:
            await timer
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_current_config_selection_preserves_project_constraint(
    app_config, config_manager, project_repository, session_maker, test_project, tmp_path_factory
):
    watcher = WatchService(
        app_config.model_copy(deep=True),
        project_repository,
        session_maker,
        constrained_project="Added Project",
    )
    added = await _register_project(
        "added-project",
        tmp_path_factory.mktemp("constrained-root"),
        config_manager,
        project_repository,
        session_maker,
    )
    assert [project.id for project in await watcher._select_projects_to_watch()] == [added.id]
