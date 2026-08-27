"""Regression tests for how the app-config dependency is resolved.

Background
----------
``basic-memory schema validate`` (and every other CLI command that drives the
API through an in-process ASGI transport) used to hang on exit instead of
returning. The cause was not in the CLI at all: ``get_app_config`` was declared
as a *sync* FastAPI dependency.

FastAPI runs sync dependencies in AnyIO's worker-thread pool. Those worker
threads are **non-daemon**, and AnyIO keeps them parked on ``queue.Queue.get``
for reuse after the task finishes. When ``asyncio.run`` returns without the
surrounding AnyIO portal having joined the pool, the interpreter reaches
``threading._shutdown`` with a live non-daemon thread that will never be woken,
and shutdown blocks forever.

Making ``get_app_config`` ``async`` keeps the dependency on the event loop, so
no worker thread is ever dispatched and nothing survives the request.

Notes for whoever touches this test next
----------------------------------------
* Do **not** rewrite this using ``fastapi.testclient.TestClient``. TestClient
  runs the app inside a blocking AnyIO portal whose teardown joins the worker
  pool, which hides the leak: the test then passes with *and* without the fix.
* The interpreter-level hang is Python 3.13 specific. Python 3.14 reaps the
  stranded worker itself, so a subprocess "does the CLI exit?" test is green on
  3.14 either way. This test asserts on the surviving thread instead of on
  process exit, so it is meaningful on both, and it was verified to fail on the
  unfixed code under Python 3.13.
"""

import threading

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from basic_memory.api.container import ApiContainer
from basic_memory.deps import AppConfigDep
from basic_memory.runtime.mode import resolve_runtime_mode


def _anyio_worker_threads() -> list[threading.Thread]:
    """Return the live, non-daemon threads that belong to AnyIO's worker pool.

    AnyIO names them ``AnyIO worker thread``. Only non-daemon threads can block
    ``threading._shutdown``, so daemon threads are deliberately excluded.
    """
    return [
        thread
        for thread in threading.enumerate()
        if thread.is_alive() and not thread.daemon and "anyio worker" in thread.name.lower()
    ]


def _app_with_config_dependency(app_config) -> FastAPI:
    app = FastAPI()
    app.state.container = ApiContainer(
        config=app_config, mode=resolve_runtime_mode(is_test_env=True)
    )

    @app.get("/config-name")
    async def read_config_name(config: AppConfigDep) -> dict[str, str]:
        return {"default_project": config.default_project}

    return app


@pytest.mark.asyncio
async def test_app_config_dependency_strands_no_worker_thread(app_config):
    """Resolving AppConfigDep must not leave a non-daemon AnyIO worker alive.

    A sync ``get_app_config`` is dispatched to AnyIO's non-daemon worker pool,
    where the thread stays parked after the request. That parked thread is what
    made the CLI hang at interpreter shutdown.
    """
    app = _app_with_config_dependency(app_config)

    workers_before = _anyio_worker_threads()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/config-name")

    assert response.status_code == 200
    assert response.json() == {"default_project": app_config.default_project}

    workers_after = _anyio_worker_threads()

    assert len(workers_after) == len(workers_before), (
        "AppConfigDep left a non-daemon AnyIO worker thread parked in the pool "
        f"(before={len(workers_before)}, after={len(workers_after)}); "
        "get_app_config must be `async def` so FastAPI resolves it on the event "
        "loop instead of dispatching a worker thread that blocks interpreter "
        "shutdown."
    )


@pytest.mark.asyncio
async def test_get_app_config_is_a_coroutine_function(app_config):
    """Guard the property the fix depends on, so a refactor cannot silently undo it."""
    import inspect

    from basic_memory.deps import get_app_config

    assert inspect.iscoroutinefunction(get_app_config), (
        "get_app_config must stay `async def`: a sync FastAPI dependency runs in a "
        "non-daemon AnyIO worker thread and strands interpreter shutdown."
    )
