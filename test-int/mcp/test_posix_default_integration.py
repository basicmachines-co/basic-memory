"""Default POSIX discovery and navigation through the MCP client transport."""

import json

import pytest
from fastmcp import Client

from basic_memory.mcp.server import set_posix_tools_visibility


POSIX_TOOLS = {"cat", "grep", "ls", "find", "tail", "man"}


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [None, False, True])
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_posix_registration_over_mcp(mcp_server, config_manager, setting, mode) -> None:
    # Omit the key entirely to exercise existing configs that inherit new defaults.
    saved = json.loads(config_manager.config_file.read_text())
    saved.pop("enable_posix_tools", None)
    if setting is not None:
        saved["enable_posix_tools"] = setting
    config_manager.config_file.write_text(json.dumps(saved))

    try:
        async with Client(mcp_server, mode=mode) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert {
                "bm_read",
                "bm_search",
                "bm_write",
                "bm_context",
                "bm_list",
                "bm_find",
                "bm_man",
                "bm_project",
            } <= tools.keys()
            instructions = client.instructions
            assert instructions is not None
            assert "bm_read" in instructions
            assert "bm_search" in instructions
            for name in ("bm_read", "bm_search", "bm_list", "bm_find", "bm_man"):
                assert tools[name].annotations.read_only_hint is True
            manual = await client.call_tool("bm_man", {})
            assert not manual.is_error
    finally:
        set_posix_tools_visibility(mcp_server, False)


@pytest.mark.asyncio
async def test_posix_default_navigation_workflow(mcp_server, app, test_project) -> None:
    """Follow advertised mount and result paths, then scan a bounded note window."""
    try:
        async with Client(mcp_server) as client:
            await client.call_tool(
                "bm_write",
                {
                    "title": "POSIX Runbook",
                    "directory": "notes",
                    "content": "# Recovery\n\nbefore\nretry the task\nafter\nremaining",
                    "metadata": {"status": "active", "priority": 3},
                    "project": test_project.name,
                },
            )
            mounts = await client.call_tool("bm_list", {"path": "/", "output_format": "json"})
            mount = next(row for row in mounts.data["nodes"] if row["name"] == test_project.name)
            note_dir = f"{mount['directory_path']}/notes"
            listing = await client.call_tool("bm_list", {"path": note_dir, "output_format": "json"})
            note_path = listing.data["nodes"][0]["file_path"]
            full = await client.call_tool(
                "bm_read", {"identifier": note_path, "output_format": "json"}
            )
            assert "retry the task" in full.data["content"]

            found = await client.call_tool(
                "bm_find", {"path": note_dir, "name": "*.md", "output_format": "json"}
            )
            assert found.data["nodes"][0]["file_path"] == note_path
            projected = await client.call_tool(
                "bm_find",
                {
                    "path": note_dir,
                    "filters": {"status": "active"},
                    "fields": ["priority"],
                    "output_format": "json",
                },
            )
            row = projected.data["results"][0]
            assert row["file_path"] == note_path
            assert row["fields"] == {"priority": 3}
            assert "content" not in row

            matches = await client.call_tool(
                "bm_search",
                {
                    "query": "retry",
                    "lines": 1,
                    "project": test_project.name,
                    "output_format": "json",
                },
            )
            window = matches.data["results"][0]["windows"][0]
            sliced = await client.call_tool(
                "bm_read",
                {
                    "identifier": note_path,
                    "start_line": window["start_line"],
                    "end_line": window["end_line"],
                    "output_format": "json",
                },
            )
            assert sliced.data["content"] == window["content"] == "before\nretry the task\nafter"
            assert len(sliced.data["content"]) < len(full.data["content"])

            recent = await client.call_tool(
                "bm_project",
                {
                    "project": test_project.name,
                    "page_size": 1,
                    "command": "recent",
                    "output_format": "json",
                },
            )
            assert recent.data["results"][0]["title"] == "POSIX Runbook"
            manual = await client.call_tool("bm_man", {"page": "bm-read"})
            assert "start_line" in manual.data
    finally:
        set_posix_tools_visibility(mcp_server, False)
