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
    - In the fixed version, SyncCoordinator.stop() awaits the task before returning,
      so by the time shutdown_db is called, the task is done (cancelled).
    """

    # Import the *module* (not the package-level FastAPI `basic_memory.api.app` export)
    # so monkeypatching affects the exact symbols referenced inside lifespan().
    #
    # Note: `basic_memory/api/__init__.py` re-exports `app`, so `import basic_memory.api.app`
    # can resolve to the FastAPI instance rather than the `basic_memory.api.app` module.
    import importlib

    api_app_module = importlib.import_module("basic_memory.api.app")
    container_module = importlib.import_module("basic_memory.api.container")
    init_module = importlib.import_module("basic_memory.services.initialization")

    # Keep startup cheap: we don't need real DB init for this ordering test.
    async def _noop_initialize_app(_app_config):
        return None

    monkeypatch.setattr(api_app_module, "initialize_app", _noop_initialize_app)

    # Patch the container's init_database to return fake objects
    async def _fake_init_database(self):
        self.engine = object()
        self.session_maker = object()
        return self.engine, self.session_maker

    monkeypatch.setattr(container_module.ApiContainer, "init_database", _fake_init_database)

    # Make the sync task long-lived so it must be cancelled on shutdown.
    # Patch at the source module where SyncCoordinator imports it.
    async def _fake_initialize_file_sync(_app_config):
        await asyncio.Event().wait()

    monkeypatch.setattr(init_module, "initialize_file_sync", _fake_initialize_file_sync)

    # Assert ordering: shutdown_db must be called only after the sync_task is done.
    # SyncCoordinator stores the task in _sync_task attribute.
    async def _assert_sync_task_done_before_db_shutdown(self):
        sync_coordinator = api_app_module.app.state.sync_coordinator
        assert sync_coordinator._sync_task is not None
        assert sync_coordinator._sync_task.done()

    monkeypatch.setattr(
        container_module.ApiContainer,
        "shutdown_database",
        _assert_sync_task_done_before_db_shutdown,
    )

    async def _run_client_once():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Any request is sufficient to trigger lifespan startup/shutdown.
            await client.get("/__nonexistent__")

    # Use asyncio.run to match the CLI/MCP execution model where loop teardown
    # would hang if a background task is left running.
    asyncio.run(_run_client_once())
