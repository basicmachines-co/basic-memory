"""Only the 1.0 tool surface is exposed, including after config enables old groups."""

import subprocess
import sys

import pytest
from fastmcp.exceptions import NotFoundError

from implementation_mcp import IMPLEMENTATION_NAMES
from basic_memory.mcp import tools
from basic_memory.mcp.server import mcp, set_project_delete_visibility

V1_TOOLS = {
    "bm_read",
    "bm_write",
    "bm_edit",
    "bm_move",
    "bm_delete",
    "bm_list",
    "bm_find",
    "bm_search",
    "bm_context",
    "bm_man",
    "bm_schema",
    "bm_project",
}
RETIRED_TOOLS = frozenset(IMPLEMENTATION_NAMES)


def test_fresh_process_exposes_only_v1_tools() -> None:
    assert set(tools.__all__) == V1_TOOLS | {"bm_project_delete"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio; import basic_memory.mcp.tools; "
            "from basic_memory.mcp.server import mcp; "
            "names = {t.name for t in asyncio.run(mcp.list_tools())}; "
            f"assert names == {V1_TOOLS!r}, names",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_config_cannot_restore_retired_tools() -> None:
    try:
        set_project_delete_visibility(mcp, True)
        registered = {tool.name: tool for tool in await mcp.list_tools()}
        assert set(registered) == V1_TOOLS | {"bm_project_delete"}
        for tool in registered.values():
            assert tool.annotations is not None
            assert tool.title == tool.annotations.title
    finally:
        set_project_delete_visibility(mcp, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(RETIRED_TOOLS))
async def test_retired_tools_cannot_be_called(name: str) -> None:
    with pytest.raises(NotFoundError):
        await mcp.call_tool(name, {})
