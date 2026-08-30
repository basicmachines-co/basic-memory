"""Tests for the bundled manual: page references, resolution, and the shipped corpus."""

from __future__ import annotations

import re

import pytest

from basic_memory.man import (
    MAN_DIR,
    ManPage,
    PageRef,
    bundled_pages,
    find_page,
    parse_page_ref,
    render_index,
)
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import __all__ as registered_tools


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("search-notes(3)", PageRef("search-notes", 3)),
        ("search-notes.3", PageRef("search-notes", 3)),
        ("search-notes-3", PageRef("search-notes", 3)),
        ("3/search-notes", PageRef("search-notes", 3)),
        ("man3/search-notes", PageRef("search-notes", 3)),
        ("man3/search-notes(3).md", PageRef("search-notes", 3)),
        ("search-notes%283%29", PageRef("search-notes", 3)),
        ("search_notes", PageRef("search-notes", None)),
        ("SEARCH_NOTES", PageRef("search-notes", None)),
        ("/write-note/", PageRef("write-note", None)),
        ("bm(1)", PageRef("bm", 1)),
    ],
)
def test_parse_page_ref_accepts_every_common_spelling(text: str, expected: PageRef) -> None:
    assert parse_page_ref(text) == expected


@pytest.mark.parametrize("text", ["", "/", "docs/search-notes"])
def test_parse_page_ref_rejects_what_cannot_name_a_page(text: str) -> None:
    with pytest.raises(ValueError, match="not a manual page reference"):
        parse_page_ref(text)


@pytest.mark.parametrize("text", ["man3", "(3)", "nope"])
def test_parse_page_ref_leaves_unknown_names_to_resolution(text: str) -> None:
    # Parse, don't validate: an odd name is still a name. It simply resolves to
    # nothing, which is the caller's "No manual entry" case, not a parse error.
    assert find_page(parse_page_ref(text)) is None


def test_find_page_uses_named_section_or_lowest() -> None:
    assert find_page(PageRef("search-notes", 3)) is not None
    assert find_page(PageRef("search-notes", None)) is not None
    assert find_page(PageRef("search-notes", 5)) is None
    assert find_page(PageRef("no-such-page", None)) is None


def test_bundled_pages_are_well_formed_and_sorted() -> None:
    pages = bundled_pages()

    assert len(pages) == len(list(MAN_DIR.glob("man[1-9]/*.md")))
    assert [(page.section, page.name) for page in pages] == sorted(
        (page.section, page.name) for page in pages
    )
    for page in pages:
        assert page.path.name == f"{page.title}.md"
        assert page.summary
        assert page.body().startswith(f"# {page.title}")
        # Pages are portable notes: the cloud manual's permalink must not ship.
        assert "permalink:" not in page.read().split("---", 2)[1]
    assert all(page.tool for page in pages if page.section == 3)


# The section-3 corpus and the tool registry are meant to match one to one. Both
# lists change deliberately; this pins the known gaps so a new tool without a page
# (or a page for a retired tool) shows up here instead of going unnoticed.
TOOLS_WITHOUT_PAGES = {"basic_memory_diagnostics"}
PAGES_WITHOUT_LOCAL_TOOLS = {"canvas", "cloud_info", "release_notes"}


def test_section_3_matches_the_tool_registry_except_known_gaps() -> None:
    documented = {page.tool for page in bundled_pages() if page.section == 3}

    assert set(registered_tools) - documented == TOOLS_WITHOUT_PAGES
    assert documented - set(registered_tools) == PAGES_WITHOUT_LOCAL_TOOLS


def _synopsis_parameters(page: ManPage) -> set[str]:
    """Parameter names in the MCP call shown under SYNOPSIS, positional or keyword."""
    synopsis = re.search(r"## SYNOPSIS\n(.*?)\n## ", page.body(), re.S)
    assert synopsis is not None, f"{page.title} has no SYNOPSIS"
    # Pages with a CLI form label the MCP block "MCP:"; MCP-only pages have one block.
    call = re.search(r"MCP:\s*```\n(.*?)```", synopsis.group(1), re.S) or re.search(
        r"```\n(.*?)```", synopsis.group(1), re.S
    )
    assert call is not None, f"{page.title} SYNOPSIS has no MCP call"
    arguments = call.group(1)[call.group(1).find("(") + 1 : call.group(1).rfind(")")]
    return set(re.findall(r"\b([a-z_][a-z0-9_]*)\b(?=\s*[=,)\n]|$)", arguments)) - {
        "none",
        "true",
        "false",
    }


@pytest.mark.asyncio
async def test_section_3_synopsis_names_every_tool_parameter() -> None:
    # The SYNOPSIS is the page's contract with the tool schema clients receive. A
    # parameter added to a tool without updating its page is exactly the drift the
    # manual exists to prevent, so it fails here rather than waiting for a reader.
    tools = {tool.name: tool for tool in await mcp.list_tools(run_middleware=False)}

    for page in bundled_pages():
        if page.section != 3 or page.tool not in tools:
            continue
        schema = set(tools[page.tool].parameters["properties"])
        documented = _synopsis_parameters(page)
        assert schema - documented == set(), f"{page.title} SYNOPSIS is missing parameters"
        assert documented - schema == set(), f"{page.title} SYNOPSIS names unknown parameters"


def test_render_index_lists_every_page_with_uri_and_summary() -> None:
    index = render_index(bundled_pages())

    assert index.startswith("# Basic Memory manual")
    assert "## Section 3 — MCP tools" in index
    for page in bundled_pages():
        assert f"- [{page.title}]({page.uri}) — {page.summary}" in index
