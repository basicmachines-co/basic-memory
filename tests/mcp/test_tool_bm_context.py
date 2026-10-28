"""bm_context: graph context from a note or pattern, routed like every bm_* tool (#1830).

These run the real ASGI stack through the shared `client` fixture.
"""

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import bm_context, write_note


async def _seed(project_name: str) -> None:
    await write_note(
        title="Node A",
        directory="graph",
        content="# Node A\n\n- [fact] A starts the chain\n\n- links_to [[Node B]]",
        project=project_name,
    )
    await write_note(
        title="Node B",
        directory="graph",
        content="# Node B\n\n- [fact] B is in the middle\n\n- links_to [[Node A]]",
        project=project_name,
    )


async def context_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_context(**arguments)
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_json_by_default_with_related_notes(client, test_project):
    await _seed(test_project.name)

    graph = await context_json(identifier="graph/node-a", project=test_project.name)

    (result,) = graph["results"]
    assert result["primary_result"]["title"] == "Node A"
    related_titles = {row.get("title") for row in result["related_results"]}
    assert "Node B" in related_titles


@pytest.mark.asyncio
async def test_explicit_project_keeps_the_identifier_a_path(client, test_project, caplog):
    # #1814: the first segment ("graph") was resolved as a project, logging a
    # client error on every call.
    await _seed(test_project.name)

    graph = await context_json(identifier="memory://graph/node-a", project=test_project.name)

    assert graph["results"][0]["primary_result"]["title"] == "Node A"
    assert "Project not found: 'graph'" not in caplog.text


@pytest.mark.asyncio
async def test_pattern_and_compact_text(client, test_project):
    await _seed(test_project.name)

    # #1812: a project-relative pattern, though permalinks here carry the project prefix.
    text = await bm_context(
        identifier="graph/*",
        compact=True,
        output_format="text",
        project=test_project.name,
    )

    assert isinstance(text, str)
    assert "Node A" in text and "Node B" in text


@pytest.mark.asyncio
async def test_compact_json_drops_bodies(client, test_project):
    await _seed(test_project.name)

    graph = await context_json(identifier="graph/node-a", compact=True, project=test_project.name)

    primary = graph["results"][0]["primary_result"]
    assert "content" not in primary
    assert "observations" not in graph["results"][0]


@pytest.mark.asyncio
async def test_an_identifier_cannot_reach_outside_the_project(client, test_project):
    """Identifiers resolve inside the project: a '../' spelling finds a project note."""
    await _seed(test_project.name)

    graph = await context_json(identifier="../graph/node-a", project=test_project.name)

    paths = [result["primary_result"]["file_path"] for result in graph["results"]]
    assert paths == ["graph/Node A.md"]


@pytest.mark.asyncio
async def test_project_qualified_start_returns_qualified_paths(client, test_project):
    """Every path in the graph can be passed back to bm_read."""
    await _seed(test_project.name)
    prefix = test_project.permalink

    graph = await context_json(identifier=f"{prefix}/graph/node-a")

    (result,) = graph["results"]
    paths = [result["primary_result"]["file_path"]]
    paths += [row["file_path"] for row in result["observations"]]
    paths += [row["file_path"] for row in result["related_results"]]
    assert paths and all(path.startswith(f"{prefix}/graph/") for path in paths)


@pytest.mark.asyncio
async def test_project_qualified_text_names_qualified_paths(client, test_project):
    """Text output carries the qualified paths too; a permalink may lack the prefix."""
    await _seed(test_project.name)
    prefix = test_project.permalink

    text = await bm_context(identifier=f"{prefix}/graph/node-a", output_format="text")

    assert isinstance(text, str)
    assert f"path: {prefix}/graph/Node A.md" in text
    assert f"[[Node B]] ({prefix}/graph/Node B.md)" in text


@pytest.mark.asyncio
async def test_text_output_names_the_next_page(client, test_project):
    """A pattern with more matches than a page says how to fetch the next one."""
    await _seed(test_project.name)

    text = await bm_context(
        identifier="graph/*", page_size=1, output_format="text", project=test_project.name
    )

    assert isinstance(text, str)
    assert "More results available. Call bm_context(identifier='graph/*'" in text
    assert "page=2, page_size=1)" in text


@pytest.mark.asyncio
async def test_malformed_locator_is_actionable_input_feedback(client, test_project):
    """The production-shaped locator must not surface as an opaque server failure (#1879)."""
    locator = "workspace/project/https://example.invalid/notes"

    async with Client(mcp) as mcp_client:
        with pytest.raises(ToolError) as exc_info:
            await mcp_client.call_tool(
                "bm_context",
                {"identifier": locator, "project": test_project.name},
            )

    message = str(exc_info.value)
    assert "protocol scheme" in message
    assert "Internal server error" not in message
