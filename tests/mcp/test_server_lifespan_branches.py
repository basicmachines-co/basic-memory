import pytest

from basic_memory import db
from basic_memory.mcp.server import lifespan, mcp


@pytest.mark.asyncio
async def test_mcp_lifespan_sync_disabled_branch(config_manager):
    cfg = config_manager.load_config()
    cfg.sync_changes = False
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_sync_enabled_branch(config_manager):
    cfg = config_manager.load_config()
    cfg.sync_changes = True
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_shuts_down_db_when_engine_was_none(config_manager):
    db._engine = None
    async with lifespan(mcp):
        pass
