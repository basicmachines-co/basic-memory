"""Integration test for project-prefixed permalink collision handling.

Verifies that notes with identical titles in different projects are
correctly disambiguated via project-prefixed permalinks and memory:// URLs.
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_permalink_collision_across_projects(mcp_server, app, test_project, tmp_path):
    """Notes with the same title in different projects resolve independently."""

    async with Client(mcp_server) as client:
        # Create a second project
        project2_path = str(tmp_path.parent / (tmp_path.name + "-collision") / "second-project")
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": "second-project",
                "project_path": project2_path,
            },
        )
        assert "second-project" in create_result.content[0].text

        # Write a note with the same title in project 1
        write1 = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Shared Title Note",
                "directory": "notes",
                "content": "# Shared Title Note\n\nContent from project ONE.",
            },
        )
        assert "Shared Title Note.md" in write1.content[0].text

        # Write a note with the same title in project 2
        write2 = await client.call_tool(
            "write_note",
            {
                "project": "second-project",
                "title": "Shared Title Note",
                "directory": "notes",
                "content": "# Shared Title Note\n\nContent from project TWO.",
            },
        )
        assert "Shared Title Note.md" in write2.content[0].text

        # Read from project 1 by title — should get project 1's content
        read1 = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Shared Title Note",
            },
        )
        read1_text = read1.content[0].text
        assert "Content from project ONE" in read1_text

        # Read from project 2 by title — should get project 2's content
        read2 = await client.call_tool(
            "read_note",
            {
                "project": "second-project",
                "identifier": "Shared Title Note",
            },
        )
        read2_text = read2.content[0].text
        assert "Content from project TWO" in read2_text

        # Permalinks should be project-prefixed and distinct
        assert f"{test_project.name}/notes/shared-title-note" in read1_text
        assert "second-project/notes/shared-title-note" in read2_text


@pytest.mark.asyncio
async def test_memory_url_routing_with_project_prefix(mcp_server, app, test_project, tmp_path):
    """memory:// URLs with project prefixes route to the correct project."""

    async with Client(mcp_server) as client:
        # Write a note in the default project
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "URL Routing Note",
                "directory": "docs",
                "content": "# URL Routing Note\n\nDefault project content.",
            },
        )

        # build_context with project-prefixed memory:// URL
        context_result = await client.call_tool(
            "build_context",
            {
                "url": f"memory://{test_project.name}/docs/url-routing-note",
                "project": test_project.name,
            },
        )
        context_text = context_result.content[0].text
        assert "URL Routing Note" in context_text
        assert "Default project content" in context_text
