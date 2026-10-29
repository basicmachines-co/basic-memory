"""Tests for the legacy `man` function in posix_tools.

No CLI command or bm_* tool calls `posix_tools.man` (`bm man` and `bm_man` read
the bundled pages directly). It stays only because
`basic_memory.mcp.tools.__init__` re-exports it; delete this file together with
the function and that re-export.
"""

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import bm_write, man


@pytest.mark.asyncio
async def test_man_index_renders_the_manual(client):
    result = await man()

    assert isinstance(result, str)
    assert result.startswith("# Basic Memory manual")
    assert "## Section 3 — MCP tools" in result


@pytest.mark.asyncio
async def test_man_bundled_page_by_reference():
    result = await man(page="bm-search(3)")

    assert isinstance(result, str)
    assert "# bm-search(3)" in result


@pytest.mark.asyncio
async def test_man_page_and_query_mutually_exclusive():
    with pytest.raises(ValueError, match="not both"):
        await man(page="bm-search(3)", query="search")


@pytest.mark.asyncio
async def test_man_reads_only_bundled_pages(client, test_project):
    """A manual project is never consulted; `project` is accepted and ignored."""
    await bm_write(
        title="posix-custom-guide",
        directory="man",
        content="# posix-custom-guide\n\nA note that is no bundled page.",
        project=test_project.name,
    )

    with pytest.raises(ToolError, match="No manual entry for posix-custom-guide"):
        await man(page="posix-custom-guide", project=test_project.name)
    with pytest.raises(ToolError, match="No manual entry for docs/unknown-guide"):
        await man(page="docs/unknown-guide")


@pytest.mark.asyncio
async def test_man_query_searches_the_bundled_pages():
    result = await man(query="frontmatter recursively", project="ignored")

    assert isinstance(result, dict)
    titles = [row["title"] for row in result["results"]]
    assert titles[:2] == ["find(1)", "bm-find(3)"]
    assert result["total"] == len(titles)
    assert result["results"][0]["uri"] == "memory://man/find(1)"
