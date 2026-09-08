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
            assert {"read_note", "search_notes", "write_note", "build_context"} <= tools.keys()
            if setting is False:
                assert POSIX_TOOLS.isdisjoint(tools)
                hidden = await client.call_tool("man", {}, raise_on_error=False)
                assert hidden.is_error
            else:
                assert POSIX_TOOLS <= tools.keys()
                for name in POSIX_TOOLS:
                    annotations = tools[name].annotations
                    assert annotations is not None
                    assert annotations.read_only_hint is True
                    assert annotations.destructive_hint is False
                instructions = client.instructions
                assert instructions is not None
                assert "Projects are mount points" in instructions
                assert 'cat(identifier="research/notes/topic.md")' in instructions
    finally:
        set_posix_tools_visibility(mcp_server, False)


@pytest.mark.asyncio
async def test_posix_default_navigation_workflow(mcp_server, app, test_project) -> None:
    """Follow advertised mount and result paths, then scan a bounded note window."""
    try:
        async with Client(mcp_server) as client:
            await client.call_tool(
                "write_note",
                {
                    "title": "POSIX Runbook",
                    "directory": "notes",
                    "content": "# Recovery\n\nbefore\nretry the task\nafter\nremaining",
                    "metadata": {"status": "active", "priority": 3},
                    "project": test_project.name,
                },
            )
            mounts = await client.call_tool("ls", {"path": "/"})
            mount = next(row for row in mounts.data["nodes"] if row["name"] == test_project.name)
            note_dir = f"{mount['directory_path']}/notes"
            listing = await client.call_tool("ls", {"path": note_dir})
            note_path = listing.data["nodes"][0]["file_path"]
            full = await client.call_tool("cat", {"identifier": note_path})
            assert "retry the task" in full.data["content"]

            found = await client.call_tool("find", {"path": note_dir, "name": "*.md"})
            assert found.data["nodes"][0]["file_path"] == note_path
            projected = await client.call_tool(
                "find",
                {"path": note_dir, "meta": ["status=active"], "fields": ["priority"]},
            )
            row = projected.data["results"][0]
            assert row["file_path"] == note_path
            assert row["fields"] == {"priority": 3}
            assert "content" not in row

            matches = await client.call_tool(
                "grep",
                {
                    "pattern": "retry",
                    "literal": True,
                    "context_lines": 1,
                    "project": test_project.name,
                },
            )
            window = matches.data["results"][0]["windows"][0]
            sliced = await client.call_tool(
                "cat",
                {
                    "identifier": note_path,
                    "start_line": window["start_line"],
                    "end_line": window["end_line"],
                },
            )
            assert sliced.data["content"] == window["content"] == "before\nretry the task\nafter"
            assert len(sliced.data["content"]) < len(full.data["content"])

            recent = await client.call_tool("tail", {"project": test_project.name, "lines": 1})
            assert recent.data[0]["title"] == "POSIX Runbook"
            manual = await client.call_tool("man", {"page": "cat(1)"})
            assert "--lines" in manual.data
    finally:
        set_posix_tools_visibility(mcp_server, False)
