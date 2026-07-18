import asyncio

import pytest

import basic_memory.index.note_content_materialization as note_content_materialization
import basic_memory.mcp.server as server_module
from basic_memory import db
from basic_memory.mcp.server import lifespan, mcp


@pytest.mark.asyncio
async def test_mcp_lifespan_sync_disabled_branch(config_manager):
    cfg = config_manager.load_config()
    cfg.index_changes = False
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_sync_enabled_branch(config_manager):
    cfg = config_manager.load_config()
    cfg.index_changes = True
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_shuts_down_db_when_engine_was_none(config_manager):
    db._engine = None
    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_drains_pending_work_before_db_shutdown(config_manager, monkeypatch):
    """Shutdown must drain queued materializations and background tasks before the
    DB closes — a 202-accepted write queued in the in-process pool would otherwise
    be cancelled at loop close and its file only appear after the next startup
    recovery sweep."""
    calls: list[str] = []

    async def record_drain_materializations() -> None:
        calls.append("drain_materializations")

    async def record_drain_background_tasks() -> None:
        calls.append("drain_background_tasks")

    real_shutdown_db = db.shutdown_db

    async def record_shutdown_db() -> None:
        calls.append("shutdown_db")
        await real_shutdown_db()

    monkeypatch.setattr(
        server_module, "drain_pending_materializations", record_drain_materializations
    )
    monkeypatch.setattr(server_module, "drain_background_tasks", record_drain_background_tasks)
    monkeypatch.setattr(db, "shutdown_db", record_shutdown_db)

    db._engine = None
    async with lifespan(mcp):
        pass

    assert calls == ["drain_materializations", "drain_background_tasks", "shutdown_db"]


@pytest.mark.asyncio
async def test_mcp_lifespan_drains_queued_materializations_on_shutdown(config_manager, monkeypatch):
    """A materialization still queued at lifespan exit completes before the exit
    returns, so one-shot MCP runs never lose an accepted write."""
    pool = note_content_materialization._MaterializationWorkerPool()
    monkeypatch.setattr(note_content_materialization, "_materialization_pool", pool)
    materialized = asyncio.Event()

    async def queued_write() -> None:
        # Multiple suspensions model a real file write + index; without the
        # shutdown drain the lifespan exits mid-job.
        for _ in range(50):
            await asyncio.sleep(0)
        materialized.set()

    async with lifespan(mcp):
        pool.submit(queued_write(), workers=1, key=(1, 1))

    assert materialized.is_set()
    await pool.aclose()
