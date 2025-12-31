"""Tests for management router API endpoints (minimal mocking).

These endpoints are mostly simple state checks and wiring; we use stub objects
and pytest monkeypatch instead of standard-library mocks.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from basic_memory.api.routers.management_router import (
    WatchStatusResponse,
    get_watch_status,
    start_watch_service,
    stop_watch_service,
)


class _Request:
    def __init__(self, app: FastAPI):
        self.app = app


class _Task:
    def __init__(self, *, done: bool):
        self._done = done
        self.cancel_called = False

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self.cancel_called = True


@pytest.fixture
def app_with_state() -> FastAPI:
    app = FastAPI()
    app.state.watch_task = None
    return app


@pytest.mark.asyncio
async def test_get_watch_status_not_running(app_with_state: FastAPI):
    app_with_state.state.watch_task = None
    resp = await get_watch_status(_Request(app_with_state))
    assert isinstance(resp, WatchStatusResponse)
    assert resp.running is False


@pytest.mark.asyncio
async def test_get_watch_status_running(app_with_state: FastAPI):
    app_with_state.state.watch_task = _Task(done=False)
    resp = await get_watch_status(_Request(app_with_state))
    assert resp.running is True


@pytest.mark.asyncio
async def test_start_watch_service_when_not_running(monkeypatch, app_with_state: FastAPI):
    app_with_state.state.watch_task = None

    created = {"watch_service": None, "task": None}

    class _StubWatchService:
        def __init__(self, *, app_config, project_repository):
            self.app_config = app_config
            self.project_repository = project_repository
            created["watch_service"] = self

    def _create_background_sync_task(sync_service, watch_service):
        created["task"] = _Task(done=False)
        return created["task"]

    # start_watch_service imports these inside the function, so patch at the source modules.
    monkeypatch.setattr("basic_memory.sync.WatchService", _StubWatchService)
    monkeypatch.setattr(
        "basic_memory.sync.background_sync.create_background_sync_task",
        _create_background_sync_task,
    )

    project_repository = object()
    sync_service = object()

    resp = await start_watch_service(_Request(app_with_state), project_repository, sync_service)
    assert resp.running is True
    assert app_with_state.state.watch_task is created["task"]
    assert created["watch_service"] is not None
    assert created["watch_service"].project_repository is project_repository


@pytest.mark.asyncio
async def test_start_watch_service_already_running(monkeypatch, app_with_state: FastAPI):
    existing = _Task(done=False)
    app_with_state.state.watch_task = existing

    def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("create_background_sync_task should not be called if already running")

    monkeypatch.setattr(
        "basic_memory.sync.background_sync.create_background_sync_task",
        _should_not_be_called,
    )

    resp = await start_watch_service(_Request(app_with_state), object(), object())
    assert resp.running is True
    assert app_with_state.state.watch_task is existing


@pytest.mark.asyncio
async def test_stop_watch_service_not_running(app_with_state: FastAPI):
    app_with_state.state.watch_task = None
    resp = await stop_watch_service(_Request(app_with_state))
    assert resp.running is False


@pytest.mark.asyncio
async def test_stop_watch_service_already_done(app_with_state: FastAPI):
    app_with_state.state.watch_task = _Task(done=True)
    resp = await stop_watch_service(_Request(app_with_state))
    assert resp.running is False


