"""The enable_posix_tools registration boundary (#1399).

The POSIX tools register on the shared FastMCP server at import time but stay
hidden until the composition root reads config in lifespan. These tests drive
both directions of the gate; visibility marks stack with the last one winning,
so each test restores the default-hidden state it started from.
"""

import pytest

from basic_memory.mcp.server import lifespan, mcp, set_posix_tools_visibility

POSIX_TOOL_NAMES = {"cat", "find", "grep", "ls", "man", "tail"}


@pytest.mark.asyncio
async def test_posix_tools_hidden_by_default():
    """Importing the tools must not change what clients see before startup."""
    names = {tool.name for tool in await mcp.list_tools()}
    assert names.isdisjoint(POSIX_TOOL_NAMES)


@pytest.mark.asyncio
async def test_posix_tools_visible_when_flag_enabled(config_manager):
    baseline = {tool.name for tool in await mcp.list_tools()}
    cfg = config_manager.load_config()
    cfg.enable_posix_tools = True
    config_manager.save_config(cfg)

    try:
        async with lifespan(mcp):
            enabled = {tool.name for tool in await mcp.list_tools()}
    finally:
        # The registration singleton is shared across tests; re-hide so later
        # tests see today's default listing (the mark appended last wins).
        set_posix_tools_visibility(mcp, False)

    assert enabled == baseline | POSIX_TOOL_NAMES
    restored = {tool.name for tool in await mcp.list_tools()}
    assert restored == baseline


@pytest.mark.asyncio
async def test_posix_tools_hidden_when_flag_disabled_through_lifespan(config_manager):
    cfg = config_manager.load_config()
    cfg.enable_posix_tools = False
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        names = {tool.name for tool in await mcp.list_tools()}

    assert names.isdisjoint(POSIX_TOOL_NAMES)
