"""
Integration tests for MCP-UI Python SDK embedded resources.

NOTE: UI tools are temporarily disabled (not registered with MCP server)
while MCP client rendering is being sorted out. These tests are skipped
until the tools are re-enabled in basic_memory.mcp.tools.__init__.
"""

import pytest
from fastmcp import Client

pytest.importorskip("mcp_ui_server")

pytestmark = pytest.mark.skip(reason="UI tools temporarily disabled")


@pytest.mark.asyncio
async def test_search_notes_ui_embedded_resource(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "SDK Note",
                "directory": "notes",
                "content": "# SDK Note\n\nThis is for embedded UI.",
                "tags": "sdk,ui",
            },
        )

        result = await client.call_tool(
            "search_notes_ui",
            {
                "project": test_project.name,
                "query": "SDK",
            },
        )

        assert len(result.content) == 1
        block = result.content[0]
        assert block.type == "resource"
        assert block.resource.mimeType == "text/html"
        assert "<!doctype html>" in block.resource.text.lower()
        assert block.resource.meta is not None
        assert "mcpui.dev/ui-initial-render-data" in block.resource.meta


@pytest.mark.asyncio
async def test_read_note_ui_embedded_resource(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "SDK Note Preview",
                "directory": "notes",
                "content": "# SDK Note Preview\n\nPreview for embedded UI.",
                "tags": "sdk,ui",
            },
        )

        result = await client.call_tool(
            "read_note_ui",
            {
                "project": test_project.name,
                "identifier": "SDK Note Preview",
            },
        )

        assert len(result.content) == 1
        block = result.content[0]
        assert block.type == "resource"
        assert block.resource.mimeType == "text/html"
        assert "<!doctype html>" in block.resource.text.lower()
        assert block.resource.meta is not None
        assert "mcpui.dev/ui-initial-render-data" in block.resource.meta
