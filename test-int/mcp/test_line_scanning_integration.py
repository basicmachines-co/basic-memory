"""The agent-visible grep → numbered read flow over the real MCP transport."""

import json

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_grep_to_read_lines_over_mcp(mcp_server, app, test_project, config_manager) -> None:
    config = config_manager.load_config()
    config.enable_posix_tools = True
    config_manager.save_config(config)
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "title": "Wire Scan",
                "directory": "test",
                "project": test_project.name,
                "content": "before\nretry the task\nafter\nremaining",
            },
        )
        found = await client.call_tool(
            "bm_search",
            {
                "query": "retry",
                "lines": 1,
                "project": test_project.name,
                "output_format": "json",
            },
        )
        block = found.content[0]
        assert block.type == "text"
        payload = json.loads(block.text)
        row = payload["results"][0]
        window = row["windows"][0]
        assert window["content"] == "before\nretry the task\nafter"
        read_args = {
            "identifier": row["external_id"],
            "project": test_project.name,
            "start_line": window["start_line"],
            "end_line": window["end_line"],
        }
        read = await client.call_tool(
            "bm_read", {**read_args, "output_format": "json", "include_frontmatter": False}
        )
        block = read.content[0]
        assert block.type == "text"
        selected = json.loads(block.text)
        assert selected["content"] == window["content"]
        assert selected["has_more"] is True
        text_read = await client.call_tool("bm_read", read_args)
        block = text_read.content[0]
        assert block.type == "text"
        assert f"{window['match_lines'][0]}: retry the task" in block.text
