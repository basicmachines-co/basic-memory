"""Only the 1.0 tool surface is exposed, including after config enables old groups."""

import subprocess
import sys

import pytest
from fastmcp.exceptions import NotFoundError

from basic_memory.mcp import tools
from basic_memory.mcp.server import (
    WRITES_TAG,
    mcp,
    set_project_delete_visibility,
    set_read_only_visibility,
)

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
    "bm_schema_validate",
    "bm_schema_infer",
    "bm_schema_diff",
    "bm_project_list",
    "bm_project_create",
    "bm_recent",
}
# Pre-1.0 tool names; none may be callable on the 1.0 server.
RETIRED_TOOLS = frozenset(
    {
        "build_context",
        "cat",
        "edit_note",
        "find",
        "grep",
        "list_memory_projects",
        "ls",
        "man",
        "read_note",
        "recent_activity",
        "search_notes",
        "tail",
        "write_note",
    }
)


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


@pytest.mark.asyncio
async def test_writes_tag_matches_read_only_annotations() -> None:
    """Every non-read-only tool carries WRITES_TAG, so read-only mode covers it.

    set_read_only_visibility hides by tag, not by annotation, so a write tool
    registered without WRITES_TAG would stay reachable in read-only mode.
    """
    try:
        set_project_delete_visibility(mcp, True)
        registered = await mcp.list_tools()
        tagged = {tool.name for tool in registered if WRITES_TAG in (tool.tags or set())}
        annotated = {
            tool.name
            for tool in registered
            if tool.annotations is not None and tool.annotations.read_only_hint is False
        }
        assert tagged == annotated
    finally:
        set_project_delete_visibility(mcp, False)


@pytest.mark.asyncio
async def test_read_only_mode_hides_and_blocks_write_tools() -> None:
    read_only_names = {
        "bm_read",
        "bm_search",
        "bm_context",
        "bm_list",
        "bm_find",
        "bm_man",
        "bm_project_list",
        "bm_recent",
        "bm_schema_validate",
        "bm_schema_infer",
        "bm_schema_diff",
    }
    try:
        set_read_only_visibility(mcp, True)
        assert {tool.name for tool in await mcp.list_tools()} == read_only_names
        for name in ("bm_write", "bm_edit", "bm_move", "bm_delete", "bm_project_create"):
            with pytest.raises(NotFoundError):
                await mcp.call_tool(name, {})
    finally:
        set_read_only_visibility(mcp, False)
        assert {tool.name for tool in await mcp.list_tools()} == V1_TOOLS


@pytest.mark.asyncio
async def test_read_only_mode_swaps_the_server_instructions() -> None:
    """The guidance must not name a tool that read-only mode has hidden.

    Instructions and the tool list reach the client in the same session, so
    text still telling the model to save with bm_write sends it after a call
    the server refuses.
    """
    try:
        set_read_only_visibility(mcp, True)
        instructions = mcp.instructions
        assert instructions is not None
        for write_tool in ("bm_write", "bm_edit", "bm_move", "bm_delete"):
            assert write_tool not in instructions, (
                f"read-only instructions must not name the hidden tool {write_tool}"
            )
        assert "bm_search" in instructions
        assert "read-only" in instructions
    finally:
        set_read_only_visibility(mcp, False)
        restored = mcp.instructions
        assert restored is not None
        assert "bm_write" in restored, "the write guidance must come back when the mode is off"
