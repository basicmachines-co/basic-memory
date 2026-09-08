"""The enable_posix_tools registration boundary (#1399).

The POSIX tools register on the shared FastMCP server at import time but stay
hidden until the composition root reads config in lifespan. These tests drive
both directions of the gate; visibility marks stack with the last one winning,
so each test restores the pre-startup hidden state it started from.
"""

import subprocess
import sys

import pytest

from basic_memory.mcp.server import lifespan, mcp, set_posix_tools_visibility

POSIX_TOOL_NAMES = {"cat", "find", "grep", "ls", "man", "tail"}


def test_posix_tools_hidden_before_startup():
    """A fresh interpreter proves import behavior independently of prior lifespans."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio; import basic_memory.mcp.tools; "
            "from basic_memory.mcp.server import mcp; "
            "names = {tool.name for tool in asyncio.run(mcp.list_tools())}; "
            "assert 'read_note' in names; "
            "assert names.isdisjoint({'cat', 'find', 'grep', 'ls', 'man', 'tail'})",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_posix_tools_visible_by_default(config_manager):
    # Other tests may have started the singleton already; establish this test
    # boundary explicitly rather than depending on suite order.
    set_posix_tools_visibility(mcp, False)
    baseline = {tool.name for tool in await mcp.list_tools()}
    cfg = config_manager.load_config()
    assert cfg.enable_posix_tools is True

    try:
        async with lifespan(mcp):
            enabled = {tool.name for tool in await mcp.list_tools()}
    finally:
        # The registration singleton is shared across tests; re-hide so later
        # tests see the pre-startup listing (the mark appended last wins).
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
