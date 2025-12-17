"""
Integration test for FastAPI lifespan shutdown behavior.

This test verifies the asyncio cancellation pattern used by the API lifespan:
when the background sync task is cancelled during shutdown, it must be *awaited*
before database shutdown begins. This prevents "hang on exit" scenarios in
`asyncio.run(...)` callers (e.g. CLI/MCP clients using httpx ASGITransport).
"""

import asyncio

from httpx import ASGITransport, AsyncClient


def test_lifespan_shutdown_awaits_sync_task_cancellation(app, monkeypatch):
    """
    Ensure lifespan shutdown awaits the cancelled background sync task.

    Why this is deterministic:
    - Cancelling a task does not make it "done" immediately; it becomes done only
      once the event loop schedules it and it processes the CancelledError.
    - In the buggy version, shutdown proceeded directly to db.shutdown_db()
      immediately after calling cancel(), so at *entry* to shutdown_db the task
      is still not done.
    - In the fixed version, lifespan does `await sync_task` before shutdown_db,
      so by the time shutdown_db is called, the task is done (cancelled).
    """

    # Import the *module* (not the package-level FastAPI `basic_memory.api.app` export)
    # so monkeypatching affects the exact symbols referenced inside lifespan().
    #
    # Note: `basic_memory/api/__init__.py` re-exports `app`, so `import basic_memory.api.app`
    # can resolve to the FastAPI instance rather than the `basic_memory.api.app` module.
    import importlib

    api_app_module = importlib.import_module("basic_memory.api.app")

    # Keep startup cheap: we don't need real DB init for this ordering test.
    async def _noop_initialize_app(_app_config):
        return None

    async def _fake_get_or_create_db(*_args, **_kwargs):
        return object(), object()

    monkeypatch.setattr(api_app_module, "initialize_app", _noop_initialize_app)
    monkeypatch.setattr(api_app_module.db, "get_or_create_db", _fake_get_or_create_db)

    # Make the sync task long-lived so it must be cancelled on shutdown.
    async def _fake_initialize_file_sync(_app_config):
        await asyncio.Event().wait()

    monkeypatch.setattr(api_app_module, "initialize_file_sync", _fake_initialize_file_sync)

    # Assert ordering: shutdown_db must be called only after the sync_task is done.
    async def _assert_sync_task_done_before_db_shutdown():
        assert api_app_module.app.state.sync_task is not None
        assert api_app_module.app.state.sync_task.done()

    monkeypatch.setattr(api_app_module.db, "shutdown_db", _assert_sync_task_done_before_db_shutdown)

    async def _run_client_once():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Any request is sufficient to trigger lifespan startup/shutdown.
            await client.get("/__nonexistent__")

    # Use asyncio.run to match the CLI/MCP execution model where loop teardown
    # would hang if a background task is left running.
    asyncio.run(_run_client_once())


