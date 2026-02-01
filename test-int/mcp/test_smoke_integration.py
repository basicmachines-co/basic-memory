"""Smoke test for MCP end-to-end flow."""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_mcp_smoke_flow(mcp_server, app, test_project):
    """Verify write -> read -> search -> build_context works end-to-end."""

    async with Client(mcp_server) as client:
        title = "Smoke Test Note"
        content = "# Smoke Test Note\n\n- [note] MCP smoke flow"

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": title,
                "directory": "smoke",
                "content": content,
                "tags": "smoke,test",
            },
        )

        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": title,
            },
        )
        assert len(read_result.content) == 1
        assert title in read_result.content[0].text

        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "Smoke Test Note",
            },
        )
        assert len(search_result.content) == 1
        assert title in search_result.content[0].text

        context_result = await client.call_tool(
            "build_context",
            {
                "project": test_project.name,
                "url": "smoke/*",
            },
        )
        assert len(context_result.content) == 1
        assert title in context_result.content[0].text
