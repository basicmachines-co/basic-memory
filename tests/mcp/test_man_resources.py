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
import basic_memory.mcp.resources.notes as notes_module
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
    assert "cloud-info(3)](memory://man/cloud-info(3)) — " in index
    assert "(hosted server only) *(tool not registered on this server)*" in index


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "memory://man/search-notes(3)",
        "memory://man/search-notes%283%29",
        "memory://man/search-notes.3",
        "memory://man/3/search-notes",
        "memory://man/man3/search-notes",
        "memory://man/search_notes",
    ],
)
async def test_any_spelling_of_a_page_reads_the_same_page(uri: str) -> None:
    page = await _read(uri)

    assert page.startswith("---\ntitle: search-notes(3)\n")
    assert "## GOTCHAS" in page


@pytest.mark.asyncio
async def test_tool_name_reaches_the_page_that_documents_it() -> None:
    page = await _read("memory://man/fetch(3)")

    assert page.startswith("---\ntitle: chatgpt-fetch(3)\n")


@pytest.mark.asyncio
async def test_unknown_pages_point_at_the_index(app, test_project) -> None:
    # The miss falls through to a note lookup in a project named man; when that
    # is a confirmed miss too, the manual's index hint is the error.
    with pytest.raises(ResourceError, match="No manual entry for nope; read memory://man"):
        await manual_page("nope")
    with pytest.raises(ResourceError, match="not a manual page reference; read memory://man"):
        await manual_page("docs/nope")


@pytest.mark.asyncio
async def test_man_template_falls_back_to_a_project_named_man(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The man template registers first and wins ties over the notes template, so
    # the note fallback must live here for the served path to reach it.
    async def note_read(identifier, context):
        assert identifier == "man/guides/setup"
        return "note from the man project"

    monkeypatch.setattr(notes_module, "read_note_markdown", note_read)

    assert await _read("memory://man/guides/setup") == "note from the man project"
