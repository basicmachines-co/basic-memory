"""
Integration tests for pagination across search and recent_activity MCP tools.

Verifies that page 1 and page 2 return disjoint result sets all the way
through to the database, and that has_more correctly signals when more
results are available.
"""

import json

import pytest
from fastmcp import Client


def _json_content(tool_result) -> dict | list:
    """Parse a FastMCP tool result content block into JSON."""
    assert len(tool_result.content) == 1
    assert tool_result.content[0].type == "text"
    return json.loads(tool_result.content[0].text)


# ---------------------------------------------------------------------------
# search_notes pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_notes_pages_are_disjoint(mcp_server, app, test_project):
    """Page 1 and page 2 of search_notes must return different results."""

    async with Client(mcp_server) as client:
        # Create 6 notes so page_size=3 gives us at least 2 full pages
        for i in range(6):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Pagination Note {i:02d}",
                    "directory": "pagination",
                    "content": f"# Pagination Note {i:02d}\n\nContent for pagination testing.",
                },
            )

        # Fetch page 1
        page1_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "Pagination Note",
                "page": 1,
                "page_size": 3,
                "output_format": "json",
            },
        )
        page1 = _json_content(page1_result)
        page1_titles = {r["title"] for r in page1["results"]}

        assert len(page1["results"]) == 3
        assert page1["has_more"] is True

        # Fetch page 2
        page2_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "Pagination Note",
                "page": 2,
                "page_size": 3,
                "output_format": "json",
            },
        )
        page2 = _json_content(page2_result)
        page2_titles = {r["title"] for r in page2["results"]}

        assert len(page2["results"]) == 3

        # The two pages must not overlap
        assert page1_titles.isdisjoint(page2_titles), (
            f"Pages overlap: {page1_titles & page2_titles}"
        )

        # Together they should cover all 6 notes
        assert len(page1_titles | page2_titles) == 6


@pytest.mark.asyncio
async def test_search_notes_has_more_becomes_false(mcp_server, app, test_project):
    """has_more should be False when the last page is reached."""

    async with Client(mcp_server) as client:
        # Create 4 notes, page_size=3 → page 1 has_more=True, page 2 has_more=False
        for i in range(4):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"LastPage Note {i:02d}",
                    "directory": "lastpage",
                    "content": f"# LastPage Note {i:02d}\n\nContent for last-page testing.",
                },
            )

        page1 = _json_content(
            await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": "LastPage Note",
                    "page": 1,
                    "page_size": 3,
                    "output_format": "json",
                },
            )
        )
        assert page1["has_more"] is True
        assert len(page1["results"]) == 3

        page2 = _json_content(
            await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": "LastPage Note",
                    "page": 2,
                    "page_size": 3,
                    "output_format": "json",
                },
            )
        )
        assert page2["has_more"] is False
        assert len(page2["results"]) == 1


# ---------------------------------------------------------------------------
# recent_activity pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_activity_pages_are_disjoint(mcp_server, app, test_project):
    """Page 1 and page 2 of recent_activity must return different results."""

    async with Client(mcp_server) as client:
        # Create 6 notes so page_size=3 gives us 2 full pages of entities
        for i in range(6):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Recent Page Note {i:02d}",
                    "directory": "recent-page",
                    "content": f"# Recent Page Note {i:02d}\n\nContent for recent-activity pagination.",
                },
            )

        # Fetch page 1 (entity-only default)
        page1_result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "page": 1,
                "page_size": 3,
                "timeframe": "7d",
                "output_format": "json",
            },
        )
        page1 = _json_content(page1_result)
        page1_titles = {item["title"] for item in page1}

        assert len(page1) == 3

        # Fetch page 2
        page2_result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "page": 2,
                "page_size": 3,
                "timeframe": "7d",
                "output_format": "json",
            },
        )
        page2 = _json_content(page2_result)
        page2_titles = {item["title"] for item in page2}

        assert len(page2) == 3

        # The two pages must not overlap
        assert page1_titles.isdisjoint(page2_titles), (
            f"Pages overlap: {page1_titles & page2_titles}"
        )

        # Together they should cover all 6 notes
        assert len(page1_titles | page2_titles) == 6


@pytest.mark.asyncio
async def test_recent_activity_has_more_becomes_false(mcp_server, app, test_project):
    """has_more should be False on the last page of recent_activity."""

    async with Client(mcp_server) as client:
        # Create 4 notes, page_size=3 → page 1 has more, page 2 does not
        for i in range(4):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Recent Last Note {i:02d}",
                    "directory": "recent-last",
                    "content": f"# Recent Last Note {i:02d}\n\nContent for has_more testing.",
                },
            )

        # Page 1: text output should mention "page=2 to see more"
        page1_result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "page": 1,
                "page_size": 3,
                "timeframe": "7d",
            },
        )
        page1_text = page1_result.content[0].text
        assert "Use page=2 to see more" in page1_text

        # Page 2: should NOT mention further pagination
        page2_result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "page": 2,
                "page_size": 3,
                "timeframe": "7d",
            },
        )
        page2_text = page2_result.content[0].text
        assert "Use page=" not in page2_text
        assert "items found" in page2_text


@pytest.mark.asyncio
async def test_recent_activity_entity_only_default_with_relations(mcp_server, app, test_project):
    """Without explicit type, recent_activity returns only entities even when
    observations and relations exist in the database."""

    async with Client(mcp_server) as client:
        # Create a note with observations and relations
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Entity With Relations",
                "directory": "dedup",
                "content": (
                    "# Entity With Relations\n\n"
                    "## Observations\n"
                    "- [status] This entity has observations\n"
                    "- [note] Another observation here\n\n"
                    "## Relations\n"
                    "- links_to [[Target Note]]\n"
                ),
            },
        )
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Target Note",
                "directory": "dedup",
                "content": "# Target Note\n\nTarget for relation.",
            },
        )

        # Default call (no type specified) should return only entities
        result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "timeframe": "7d",
                "output_format": "json",
            },
        )
        payload = _json_content(result)
        assert isinstance(payload, list)
        assert len(payload) >= 2
        assert all(item["type"] == "entity" for item in payload)

        # Explicit type=["entity", "observation", "relation"] returns mixed types
        all_types_result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "timeframe": "7d",
                "type": ["entity", "observation", "relation"],
                "output_format": "json",
            },
        )
        all_payload = _json_content(all_types_result)
        types_returned = {item["type"] for item in all_payload}
        # Observations and relations should now appear alongside entities
        assert "entity" in types_returned
        assert len(all_payload) > len(payload), (
            "Requesting all types should return more items than entity-only"
        )
