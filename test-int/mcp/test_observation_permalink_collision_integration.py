"""Regression tests for https://github.com/basicmachines-co/basic-memory/issues/909.

Observation content is truncated to 200 chars when building the synthetic
permalink (Postgres btree index limit), so distinct observations sharing a
category and a 200-char content prefix used to collide and the second one was
silently dropped from the search index, making it unfindable. #931 fixed this
by appending a content digest to the truncated permalink.

These tests pin the end-to-end behavior independent of the disambiguation
mechanism: every observation stays searchable, and deleting the note removes
every index row — including rows whose permalinks needed disambiguation.
"""

import json
from typing import Any

import pytest
from fastmcp import Client


def _json_content(tool_result) -> Any:
    """Parse a FastMCP tool result content block into JSON."""
    assert len(tool_result.content) == 1
    assert tool_result.content[0].type == "text"
    return json.loads(tool_result.content[0].text)  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_duplicate_category_content_observations_both_searchable(
    mcp_server, app, test_project
):
    """Both observations must be indexed even when their synthetic permalinks collide."""
    async with Client(mcp_server) as client:
        prefix = "x" * 210  # > 200 so the truncated permalink prefixes are identical
        content = (
            "# Dup Obs Note\n\n"
            "## Observations\n"
            f"- [note] {prefix} ALPHA_UNIQUE_MARKER\n"
            f"- [note] {prefix} BETA_UNIQUE_MARKER\n"
        )
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Dup Obs Note",
                "directory": "dup",
                "content": content,
            },
        )

        # Both observations should be independently findable by their unique suffix
        for marker in ("ALPHA_UNIQUE_MARKER", "BETA_UNIQUE_MARKER"):
            result = await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": marker,
                    "search_type": "text",
                    "entity_types": ["observation"],
                    "output_format": "json",
                },
            )
            data = _json_content(result)
            snippets = [r.get("content") or "" for r in data["results"]]
            assert any(marker in s for s in snippets), (
                f"observation containing {marker} was dropped from the search index "
                "due to a synthetic-permalink collision (content truncated to 200 chars). "
                "results=" + json.dumps(data, default=str)[:800]
            )


@pytest.mark.asyncio
async def test_delete_note_with_colliding_observations_leaves_no_ghost_rows(
    mcp_server, app, test_project, search_service
):
    """Deleting a note must clean up disambiguated observation index rows.

    search_index has no FK cascade from entity, so any index-time permalink
    disambiguation must be matched by delete-time cleanup or the extra
    observation survives in the search index as a ghost row pointing at the
    deleted file.

    The post-delete assertions inspect the index through ``search_service``
    rather than the ``search_notes`` tool: the MCP search pipeline happens to
    hide rows whose entity is gone, which would mask the orphan.
    """
    from basic_memory.schemas.search import SearchQuery

    async with Client(mcp_server) as client:
        prefix = "y" * 210  # > 200 so the truncated permalink prefixes are identical
        content = (
            "# Ghost Obs Note\n\n"
            "## Observations\n"
            f"- [note] {prefix} GHOST_ALPHA_MARKER\n"
            f"- [note] {prefix} GHOST_BETA_MARKER\n"
        )
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Ghost Obs Note",
                "directory": "ghost",
                "content": content,
            },
        )

        # Both observations are searchable before deletion
        for marker in ("GHOST_ALPHA_MARKER", "GHOST_BETA_MARKER"):
            result = await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": marker,
                    "search_type": "text",
                    "entity_types": ["observation"],
                    "output_format": "json",
                },
            )
            data = _json_content(result)
            assert any(marker in (r.get("content") or "") for r in data["results"])

        # Both rows exist in the search index itself under distinct permalinks
        index_rows = await search_service.search(SearchQuery(text="GHOST_BETA_MARKER"))
        assert any(r.type == "observation" for r in index_rows)

        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "Ghost Obs Note",
            },
        )
        assert "true" in delete_result.content[0].text.lower()  # pyright: ignore [reportAttributeAccessIssue]

        # No ghost rows remain in the index - including any disambiguated row
        for marker in ("GHOST_ALPHA_MARKER", "GHOST_BETA_MARKER"):
            index_rows = await search_service.search(SearchQuery(text=marker))
            assert index_rows == [], (
                f"search index row containing {marker} survived note deletion as a ghost: "
                f"{[(r.type, r.permalink) for r in index_rows]}"
            )
