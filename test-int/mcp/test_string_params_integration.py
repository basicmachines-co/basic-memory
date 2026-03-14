"""Integration tests for MCP tools accepting string-serialized list/dict params.

Goes through the full FastMCP Client → validate_call → tool function path,
which is where Pydantic rejects strings for list/dict params.
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_search_notes_entity_types_as_string(mcp_server, app, test_project):
    """search_notes should accept entity_types as a JSON string via MCP protocol."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Entity Type Coerce Test",
                "directory": "test",
                "content": "# Test\nContent for entity type coercion",
            },
        )

        # MCP client sends entity_types as a string
        result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "coercion",
                "entity_types": '["entity"]',
            },
        )
        text = result.content[0].text
        assert "Search Failed" not in text


@pytest.mark.asyncio
async def test_search_notes_note_types_as_string(mcp_server, app, test_project):
    """search_notes should accept note_types as a JSON string via MCP protocol."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Note Type Coerce Test",
                "directory": "test",
                "content": "# Test\nContent for note type coercion",
            },
        )

        result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "coercion",
                "note_types": '["note"]',
            },
        )
        text = result.content[0].text
        assert "Search Failed" not in text


@pytest.mark.asyncio
async def test_search_notes_tags_as_string(mcp_server, app, test_project):
    """search_notes should accept tags as a JSON string via MCP protocol."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Tags Coerce Test",
                "directory": "test",
                "content": "# Test\nTagged content for coercion",
                "tags": "alpha",
            },
        )

        result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "tagged",
                "tags": '["alpha"]',
            },
        )
        text = result.content[0].text
        assert "Search Failed" not in text


@pytest.mark.asyncio
async def test_search_notes_metadata_filters_as_string(mcp_server, app, test_project):
    """search_notes should accept metadata_filters as a JSON string via MCP protocol."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Metadata Coerce Test",
                "directory": "test",
                "content": "# Test\nMetadata content for coercion",
            },
        )

        result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "metadata",
                "metadata_filters": '{"type": "note"}',
            },
        )
        text = result.content[0].text
        assert "Search Failed" not in text


@pytest.mark.asyncio
async def test_write_note_metadata_as_string(mcp_server, app, test_project):
    """write_note should accept metadata as a JSON string via MCP protocol."""
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "String Metadata Note",
                "directory": "test",
                "content": "# Test\nWith string metadata",
                "metadata": '{"priority": "high"}',
            },
        )
        text = result.content[0].text
        assert "Created note" in text or "Updated note" in text


@pytest.mark.asyncio
async def test_canvas_nodes_edges_as_string(mcp_server, app, test_project):
    """canvas should accept nodes and edges as JSON strings via MCP protocol."""
    import json

    nodes = [
        {
            "id": "n1",
            "type": "text",
            "text": "Hello",
            "x": 0,
            "y": 0,
            "width": 200,
            "height": 100,
        }
    ]
    edges = [{"id": "e1", "fromNode": "n1", "toNode": "n1", "label": "self"}]

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "canvas",
            {
                "project": test_project.name,
                "title": "Coerce Canvas Test",
                "directory": "test",
                "nodes": json.dumps(nodes),
                "edges": json.dumps(edges),
            },
        )
        text = result.content[0].text
        assert "Created" in text or "Updated" in text
