"""Tests for the manual as MCP resources (memory://man and memory://man/<page>)."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ResourceError

from basic_memory.man import bundled_pages
from basic_memory.mcp.resources.man import (
    MANUAL_INDEX_URI,
    MANUAL_PAGE_TEMPLATE,
    manual_index,
    manual_page,
)
from basic_memory.mcp.server import mcp


async def _read(uri: str) -> str:
    result = await mcp.read_resource(uri)
    content = result.contents[0].content
    assert isinstance(content, str)
    return content


@pytest.mark.asyncio
async def test_every_page_is_a_listed_resource_and_the_template_is_registered() -> None:
    listed = {str(resource.uri): resource for resource in await mcp.list_resources()}
    templates = {str(template.uri_template) for template in await mcp.list_resource_templates()}

    assert MANUAL_INDEX_URI in listed
    assert MANUAL_PAGE_TEMPLATE in templates
    for page in bundled_pages():
        assert page.uri in listed
        assert listed[page.uri].description == page.summary
        assert listed[page.uri].mime_type == "text/markdown"


@pytest.mark.asyncio
async def test_index_resource_links_every_page() -> None:
    index = await _read(MANUAL_INDEX_URI)

    assert index == await manual_index()
    for page in bundled_pages():
        assert page.uri in index
    # Served from a local server, the hosted-only page is marked, not offered.
    hosted_only = next(line for line in index.splitlines() if "memory://man/bm-agent(3)" in line)
    assert hosted_only.endswith("*(tool not registered on this server)*")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "memory://man/bm-search(3)",
        "memory://man/bm-search%283%29",
        "memory://man/bm-search.3",
        "memory://man/3/bm-search",
        "memory://man/man3/bm-search",
        "memory://man/bm_search",
    ],
)
async def test_any_spelling_of_a_page_reads_the_same_page(uri: str) -> None:
    page = await _read(uri)

    assert page.startswith("---\ntitle: bm-search(3)\n")
    assert "## GOTCHAS" in page


@pytest.mark.asyncio
async def test_tool_name_reaches_the_page_that_documents_it() -> None:
    page = await _read("memory://man/fetch(3)")

    assert page.startswith("---\ntitle: chatgpt-fetch(3)\n")


@pytest.mark.asyncio
async def test_unknown_pages_point_at_the_index(app, test_project) -> None:
    # No project may be named man, so a miss is the manual's own, with the index hint.
    with pytest.raises(ResourceError, match="No manual entry for nope; read memory://man"):
        await manual_page("nope")
    with pytest.raises(ResourceError, match="not a manual page reference; read memory://man"):
        await manual_page("docs/nope")
