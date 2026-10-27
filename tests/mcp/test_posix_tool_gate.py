"""Saved POSIX visibility settings cannot restore retired MCP registrations."""

import pytest

from basic_memory.mcp.server import lifespan, mcp


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_legacy_posix_setting_cannot_restore_tools(config_manager, enabled):
    config = config_manager.load_config()
    config.enable_posix_tools = enabled
    config_manager.save_config(config)
    async with lifespan(mcp):
        names = {tool.name for tool in await mcp.list_tools()}
    assert "bm_read" in names
    assert all(name.startswith("bm_") for name in names)
