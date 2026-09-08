"""Compact discovery preserves ranked note identities without source prose."""

import json

import pytest
from fastmcp import Client


@pytest.mark.asyncio
@pytest.mark.parametrize("all_projects", [False, True])
async def test_compact_search_can_discover_then_read_notes(
    mcp_server, app, test_project, all_projects
):
    body = "CompactNeedle " + "This is source prose for a long note. " * 250
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "title": "CompactNeedle",
                "content": body,
                "directory": "search",
                "project": test_project.name,
            },
        )
        arguments: dict[str, object] = {
            "query": "CompactNeedle",
            "search_type": "text",
            "output_format": "json",
        }
        if all_projects:
            arguments["search_all_projects"] = True
        else:
            arguments["project"] = test_project.name
        full_result = await client.call_tool("search_notes", arguments)
        compact_result = await client.call_tool("search_notes", {**arguments, "compact": True})
        full = json.loads(full_result.content[0].text)
        compact = json.loads(compact_result.content[0].text)
        assert full["results"]
        assert "content" in full["results"][0]
        omitted = {"content", "matched_chunk", "content_length", "content_truncated"}
        expected = {
            **full,
            "results": [
                {key: value for key, value in row.items() if key not in omitted}
                for row in full["results"]
            ],
        }
        assert compact == expected
        assert len(json.dumps(compact)) < len(json.dumps(full)) / 3

        selected = compact["results"][0]
        read = await client.call_tool(
            "read_note",
            {"identifier": selected["external_id"], "project": test_project.name},
        )
        assert body in read.content[0].text

        text_result = await client.call_tool(
            "search_notes", {**arguments, "compact": True, "output_format": "text"}
        )
        assert "CompactNeedle" in text_result.content[0].text
        assert "This is source prose" not in text_result.content[0].text
