"""Integration tests for MCP resources: the manual and notes over resources/read.

Full flow, no mocks: MCP Client → MCP Server → FastAPI (ASGI) → database. This is
what an actual MCP client does with the `memory://` URIs Basic Memory hands out.
"""

from typing import Any

import pytest
from fastmcp import Client

# The mcp_server fixture registers tools, resources, and prompts.


async def read_text(client: Client[Any], uri: str) -> str:
    contents = await client.read_resource(uri)
    text = getattr(contents[0], "text", None)
    assert isinstance(text, str)
    return text


@pytest.mark.asyncio
async def test_manual_resources_are_listed_and_readable(mcp_server, app):
    """The manual index and pages answer resources/list and resources/read."""
    async with Client(mcp_server) as client:
        listed = {str(resource.uri) for resource in await client.list_resources()}
        assert "memory://man" in listed
        assert "memory://man/search-notes(3)" in listed

        index = await read_text(client, "memory://man")
        assert index.startswith("# Basic Memory manual")

        # Any common spelling of a page resolves through the template.
        page = await read_text(client, "memory://man/search-notes(3)")
        by_tool_name = await read_text(client, "memory://man/search_notes")
        assert page.startswith("---\ntitle: search-notes(3)\n")
        assert by_tool_name == page


@pytest.mark.asyncio
async def test_note_is_readable_at_its_memory_uri(mcp_server, app, test_project):
    """A note written through the tools reads back as raw markdown via its URI."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Search Design",
                "directory": "specs",
                "content": (
                    "# Search Design\n\n"
                    "- [decision] notes answer resources/read #mcp\n"
                    "- relates_to [[Indexing]]\n"
                ),
            },
        )

        # Project-prefixed canonical URI.
        text = await read_text(client, f"memory://{test_project.name}/specs/search-design")
        assert text.startswith("---\n")  # raw file: frontmatter included
        assert "- [decision] notes answer resources/read #mcp" in text

        # Unprefixed spelling: the first segment is a directory, not a project,
        # so routing falls back to the active/default project.
        unprefixed = await read_text(client, "memory://specs/search-design")
        assert unprefixed == text

        # File-path spelling.
        by_path = await read_text(client, f"memory://{test_project.name}/specs/search-design.md")
        assert by_path == text


@pytest.mark.asyncio
async def test_project_info_uri_reads_over_the_wire(mcp_server, app, test_project):
    """The workspace/project/info template serves JSON stats through a real session."""
    async with Client(mcp_server) as client:
        info = await read_text(client, f"memory://local/{test_project.permalink}/info")
        assert test_project.name in info


@pytest.mark.asyncio
async def test_unknown_note_reports_a_missing_note(mcp_server, app, test_project):
    """A miss surfaces as an error naming the note, not a fuzzy match or silence."""
    async with Client(mcp_server) as client:
        with pytest.raises(Exception, match="No note"):
            await client.read_resource(f"memory://{test_project.name}/nope/does-not-exist")
