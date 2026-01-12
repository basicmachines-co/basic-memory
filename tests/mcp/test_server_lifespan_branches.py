import pytest

from basic_memory import db
from basic_memory.mcp.server import lifespan, mcp


@pytest.mark.asyncio
async def test_mcp_lifespan_sync_disabled_branch(config_manager, monkeypatch):
    cfg = config_manager.load_config()
    cfg.sync_changes = False
    cfg.cloud_mode = False
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_cloud_mode_branch(config_manager):
    cfg = config_manager.load_config()
    cfg.sync_changes = True
    cfg.cloud_mode = True
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_shuts_down_db_when_engine_was_none(config_manager):
    db._engine = None
    async with lifespan(mcp):
        pass
