"""Tests for watch service project reloading functionality (minimal mocking).

We avoid standard-library mocks in favor of:
- small stub repo/task objects
- pytest monkeypatch for swapping asyncio.sleep / watchfiles.awatch when needed
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.models.project import Project
from basic_memory.sync.watch_service import WatchService


@dataclass
class _Repo:
    projects_side_effect: list[list[Project]] | None = None
    projects_return: list[Project] | None = None

    def __post_init__(self):
        self.calls = 0

    async def get_active_projects(self):
        self.calls += 1
        if self.projects_side_effect is not None:
            idx = min(self.calls - 1, len(self.projects_side_effect) - 1)
            return self.projects_side_effect[idx]
        return self.projects_return or []


@pytest.mark.asyncio
async def test_schedule_restart_uses_config_interval(monkeypatch):
    config = BasicMemoryConfig(watch_project_reload_interval=2)
    repo = _Repo()
    watch_service = WatchService(config, repo, quiet=True)

    stop_event = asyncio.Event()
    slept: list[int] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await watch_service._schedule_restart(stop_event)

    assert slept == [2]
    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_watch_projects_cycle_handles_empty_project_list(monkeypatch):
    config = BasicMemoryConfig()
    repo = _Repo()
    watch_service = WatchService(config, repo, quiet=True)

    stop_event = asyncio.Event()
    stop_event.set()

    captured = {"args": None, "kwargs": None}

    async def awatch_stub(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        if False:  # pragma: no cover
            yield None
        return

    monkeypatch.setattr("basic_memory.sync.watch_service.awatch", awatch_stub)

    await watch_service._watch_projects_cycle([], stop_event)

    assert captured["args"] == ()
    assert captured["kwargs"]["debounce"] == config.sync_delay
    assert captured["kwargs"]["watch_filter"] == watch_service.filter_changes
    assert captured["kwargs"]["recursive"] is True
    assert captured["kwargs"]["stop_event"] is stop_event


@pytest.mark.asyncio
async def test_run_handles_no_projects(monkeypatch):
    config = BasicMemoryConfig()
    repo = _Repo(projects_return=[])
    watch_service = WatchService(config, repo, quiet=True)

    slept: list[int] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        # Stop after first sleep
        watch_service.state.running = False
        return None

    async def fake_write_status():
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(watch_service, "write_status", fake_write_status)

    await watch_service.run()

    assert slept and slept[-1] == config.watch_project_reload_interval


@pytest.mark.asyncio
async def test_run_reloads_projects_each_cycle(monkeypatch, tmp_path):
    config = BasicMemoryConfig(watch_project_reload_interval=1)
    repo = _Repo(
        projects_side_effect=[
            [Project(id=1, name="project1", path=str(tmp_path / "p1"), permalink="project1")],
            [
                Project(id=1, name="project1", path=str(tmp_path / "p1"), permalink="project1"),
                Project(id=2, name="project2", path=str(tmp_path / "p2"), permalink="project2"),
            ],
        ]
    )
    watch_service = WatchService(config, repo, quiet=True)

    cycle_count = 0

    async def watch_cycle_stub(projects, stop_event):
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count >= 2:
            watch_service.state.running = False
        stop_event.set()

    async def fake_write_status():
        return None

    monkeypatch.setattr(watch_service, "_watch_projects_cycle", watch_cycle_stub)
    monkeypatch.setattr(watch_service, "write_status", fake_write_status)

    await watch_service.run()

    assert repo.calls == 2
    assert cycle_count == 2


@pytest.mark.asyncio
async def test_run_filters_cloud_only_projects_each_cycle(monkeypatch, tmp_path):
    """Cloud-only projects (slug path, no local directory) are filtered out."""
    config = BasicMemoryConfig(
        watch_project_reload_interval=1,
        projects={
            "local-project": {"path": str(tmp_path / "local"), "mode": "local"},
            "cloud-only": {"path": "cloud-slug", "mode": "cloud"},
        },
    )
    repo = _Repo(
        projects_return=[
            Project(id=1, name="local-project", path=str(tmp_path / "local"), permalink="local"),
            Project(id=2, name="cloud-only", path="cloud-slug", permalink="cloud-only"),
        ]
    )
    watch_service = WatchService(config, repo, quiet=True)

    seen_project_names: list[list[str]] = []

    async def watch_cycle_stub(projects, stop_event):
        seen_project_names.append([p.name for p in projects])
        watch_service.state.running = False
        stop_event.set()

    async def fake_write_status():
        return None

    monkeypatch.setattr(watch_service, "_watch_projects_cycle", watch_cycle_stub)
    monkeypatch.setattr(watch_service, "write_status", fake_write_status)

    await watch_service.run()

    assert seen_project_names == [["local-project"]]


@pytest.mark.asyncio
async def test_run_keeps_cloud_projects_with_local_bisync(monkeypatch, tmp_path):
    """Cloud projects with an absolute path (local bisync copy) are kept for watching."""
    config = BasicMemoryConfig(
        watch_project_reload_interval=1,
        projects={
            "local-project": {"path": str(tmp_path / "local"), "mode": "local"},
            "cloud-bisync": {"path": str(tmp_path / "cloud"), "mode": "cloud"},
        },
    )
    repo = _Repo(
        projects_return=[
            Project(id=1, name="local-project", path=str(tmp_path / "local"), permalink="local"),
            Project(
                id=2,
                name="cloud-bisync",
                path=str(tmp_path / "cloud"),
                permalink="cloud-bisync",
            ),
        ]
    )
    watch_service = WatchService(config, repo, quiet=True)

    seen_project_names: list[list[str]] = []

    async def watch_cycle_stub(projects, stop_event):
        seen_project_names.append([p.name for p in projects])
        watch_service.state.running = False
        stop_event.set()

    async def fake_write_status():
        return None

    monkeypatch.setattr(watch_service, "_watch_projects_cycle", watch_cycle_stub)
    monkeypatch.setattr(watch_service, "write_status", fake_write_status)

    await watch_service.run()

    assert seen_project_names == [["local-project", "cloud-bisync"]]


@pytest.mark.asyncio
async def test_run_continues_after_cycle_error(monkeypatch, tmp_path):
    config = BasicMemoryConfig()
    repo = _Repo(
        projects_return=[Project(id=1, name="test", path=str(tmp_path / "test"), permalink="test")]
    )
    watch_service = WatchService(config, repo, quiet=True)

    call_count = 0
    slept: list[int] = []

    async def failing_watch_cycle(_projects, _stop_event):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Simulated error")
        watch_service.state.running = False

    async def fake_sleep(seconds):
        slept.append(seconds)
        return None

    async def fake_write_status():
        return None

    monkeypatch.setattr(watch_service, "_watch_projects_cycle", failing_watch_cycle)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(watch_service, "write_status", fake_write_status)

    await watch_service.run()

    assert call_count == 2
    assert 5 in slept  # error backoff


@pytest.mark.asyncio
async def test_timer_task_cancelled_properly(monkeypatch, tmp_path):
    config = BasicMemoryConfig()
    repo = _Repo(
        projects_return=[Project(id=1, name="test", path=str(tmp_path / "test"), permalink="test")]
    )
    watch_service = WatchService(config, repo, quiet=True)

    created_tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def track_create_task(coro):
        task = real_create_task(coro)
        created_tasks.append(task)
        return task

    # Make _schedule_restart never complete unless cancelled.
    async def long_sleep(_seconds):
        fut = asyncio.Future()
        return await fut

    async def quick_watch_cycle(_projects, _stop_event):
        watch_service.state.running = False

    async def fake_write_status():
        return None

    monkeypatch.setattr(asyncio, "create_task", track_create_task)
    monkeypatch.setattr(asyncio, "sleep", long_sleep)
    monkeypatch.setattr(watch_service, "_watch_projects_cycle", quick_watch_cycle)
    monkeypatch.setattr(watch_service, "write_status", fake_write_status)

    await watch_service.run()

    assert len(created_tasks) == 1
    timer_task = created_tasks[0]
    assert timer_task.cancelled() or timer_task.done()


@pytest.mark.asyncio
async def test_new_project_addition_scenario(monkeypatch, tmp_path):
    config = BasicMemoryConfig(
        projects={
            "existing": {"path": str(tmp_path / "existing"), "mode": "local"},
            "new": {"path": str(tmp_path / "new"), "mode": "local"},
        },
    )

    initial_projects = [
        Project(id=1, name="existing", path=str(tmp_path / "existing"), permalink="existing")
    ]
    updated_projects = [
        Project(id=1, name="existing", path=str(tmp_path / "existing"), permalink="existing"),
        Project(id=2, name="new", path=str(tmp_path / "new"), permalink="new"),
    ]

    repo = _Repo(projects_side_effect=[initial_projects, initial_projects, updated_projects])
    watch_service = WatchService(config, repo, quiet=True)

    cycle_count = 0
    project_lists_used: list[list[Project]] = []

    async def counting_watch_cycle(projects, stop_event):
        nonlocal cycle_count
        cycle_count += 1
        project_lists_used.append(list(projects))
        if cycle_count >= 3:
            watch_service.state.running = False
        stop_event.set()

    async def fake_write_status():
        return None

    monkeypatch.setattr(watch_service, "_watch_projects_cycle", counting_watch_cycle)
    monkeypatch.setattr(watch_service, "write_status", fake_write_status)

    await watch_service.run()

    assert repo.calls >= 3
    assert cycle_count == 3
    assert any(len(p) == 1 for p in project_lists_used)
    assert any(len(p) == 2 for p in project_lists_used)
