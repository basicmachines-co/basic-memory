"""Tests for the bundled manual: page references, resolution, and the shipped corpus."""

from __future__ import annotations

import re

import pytest

from basic_memory.man import (
    MAN_DIR,
    PageRef,
    bundled_pages,
    declare_registry_ownership,
    extract_mcp_synopsis,
    find_page,
    parse_page_ref,
    render_index,
    render_synopsis,
    replace_mcp_synopsis,
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


def test_find_page_accepts_the_tool_name_as_an_alias() -> None:
    # chatgpt-search(3) documents the `search` tool; memory://man/search(3) must land there.
    by_alias = find_page(PageRef("search", 3))
    without_section = find_page(PageRef("fetch", None))
    exact = find_page(PageRef("search-notes", 3))
    assert by_alias is not None and by_alias.name == "chatgpt-search"
    assert without_section is not None and without_section.name == "chatgpt-fetch"
    assert exact is not None and exact.name == "search-notes"


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
TOOLS_WITHOUT_PAGES: set[str] = set()
PAGES_WITHOUT_LOCAL_TOOLS = {"cloud_info"}  # hosted-only; see cloud-info(3)


def test_section_3_matches_the_tool_registry_except_known_gaps() -> None:
    documented = {page.tool for page in bundled_pages() if page.section == 3}

    assert set(registered_tools) - documented == TOOLS_WITHOUT_PAGES
    assert documented - set(registered_tools) == PAGES_WITHOUT_LOCAL_TOOLS


def test_render_synopsis_orders_required_first_and_wraps() -> None:
    parameters = {
        "required": ["query"],
        "properties": {
            "alpha": {"default": None},
            "query": {"type": "string"},
            "flag": {"default": False},
            "mode": {"default": "text"},
            "count": {"default": 10},
        },
    }
    assert (
        render_synopsis("demo", parameters)
        == 'demo(query, alpha=None, flag=False, mode="text", count=10)'
    )

    wide = {"required": [], "properties": {f"parameter_{i}": {"default": None} for i in range(9)}}
    rendered = render_synopsis("demo_tool", wide)
    assert all(len(line) <= 76 for line in rendered.splitlines())
    assert rendered.splitlines()[1].startswith(" " * len("demo_tool("))
    assert rendered.endswith(")")
    assert render_synopsis("bare", {"properties": {}}) == "bare()"
    # A control character in a default must be escaped, not embedded literally.
    tricky = {"properties": {"sep": {"default": "a\nb"}, "q": {"default": 'say "hi"'}}}
    assert render_synopsis("demo", tricky) == 'demo(sep="a\\nb", q="say \\"hi\\"")'
    # A default factory leaves no schema default; the parameter must still read
    # as optional (name=...), never as a bare required name.
    factory = {"required": ["query"], "properties": {"query": {}, "tags": {}}}
    assert render_synopsis("demo", factory) == "demo(query, tags=...)"


def test_replace_mcp_synopsis_touches_only_the_mcp_block() -> None:
    labelled = "# t\n\n## SYNOPSIS\n\nMCP:\n\n```\nold()\n```\n\nCLI:\n\n```\nbm t\n```\n\n## DESCRIPTION\n"
    bare = "# t\n\n## SYNOPSIS\n\n```\nold()\n```\n\n## DESCRIPTION\n"

    replaced = replace_mcp_synopsis(labelled, "new(a, b=1)")
    assert extract_mcp_synopsis(replaced) == "new(a, b=1)"
    assert "```\nbm t\n```" in replaced  # the CLI block is not the generator's to rewrite
    assert extract_mcp_synopsis(replace_mcp_synopsis(bare, "new()")) == "new()"
    with pytest.raises(ValueError, match="no MCP SYNOPSIS block"):
        replace_mcp_synopsis("# t\n\n## DESCRIPTION\n", "new()")
    with pytest.raises(ValueError, match="no MCP SYNOPSIS block"):
        extract_mcp_synopsis("# t\n\n## DESCRIPTION\n")


@pytest.mark.asyncio
async def test_section_3_synopsis_is_exactly_the_registry_rendering() -> None:
    # The MCP SYNOPSIS block is a mechanical section owned by the registry
    # generator: byte-equal to the rendering of the schema clients receive. A tool
    # change without regenerating the pages fails here, pointing at the fix.
    tools = {tool.name: tool for tool in await mcp.list_tools(run_middleware=False)}

    for page in bundled_pages():
        if page.section != 3 or page.tool not in tools:
            continue
        expected = render_synopsis(page.tool, tools[page.tool].parameters)
        assert extract_mcp_synopsis(page.read()) == expected, (
            f"{page.title} SYNOPSIS is stale; run `just man-regen` and commit the result"
        )


def test_declare_registry_ownership_touches_frontmatter_only() -> None:
    # A curated body may contain a literal `generated: hand` line (a YAML example);
    # only the opening frontmatter block is the generator's to rewrite.
    page = (
        "---\ntitle: t(3)\ngenerated: hand\ntool: t\n---\n\n# t(3)\n\n"
        "```yaml\ngenerated: hand\n```\n"
    )

    flipped = declare_registry_ownership(page)

    assert flipped.startswith("---\ntitle: t(3)\ngenerated: registry\ntool: t\n---\n")
    assert "```yaml\ngenerated: hand\n```" in flipped
    assert declare_registry_ownership(flipped) == flipped


def test_registry_pages_declare_registry_ownership() -> None:
    # generated: declares who may rewrite the mechanical sections. Every page whose
    # tool this build registers is generator-managed; hosted-only pages stay hand.
    for page in bundled_pages():
        if page.section != 3:
            continue
        expected = "registry" if page.tool in set(registered_tools) else "hand"
        assert page.generated == expected, f"{page.title} declares generated: {page.generated}"


def test_section_3_links_resolve_to_bundled_pages() -> None:
    # Section 3 ships in full, so a [[name(3)]] link with no page behind it is a
    # dangling SEE ALSO: a retired tool's page was dropped but not its references.
    for page in bundled_pages():
        for name in re.findall(r"\[\[([^\]]+)\(3\)\]\]", page.body()):
            assert find_page(PageRef(name, 3)) is not None, (
                f"{page.title} links to {name}(3), which is not bundled"
            )


@pytest.mark.parametrize("stale", ["pending release", "unreleased", "fixed at HEAD"])
def test_pages_carry_no_release_pending_claims(stale: str) -> None:
    # The pages ship with the code, so a fix described as pending or unreleased is
    # already in every package that carries the page; such a note is always stale.
    for page in bundled_pages():
        assert stale not in page.body().lower(), f"{page.title} still says '{stale}'"


def test_render_index_marks_pages_whose_tool_this_server_lacks() -> None:
    index = render_index(bundled_pages(), registered_tools=frozenset(registered_tools))
    hosted_only = find_page(PageRef("cloud-info", 3))
    local = find_page(PageRef("search-notes", 3))
    assert hosted_only is not None and local is not None

    assert f"({hosted_only.uri}) — {hosted_only.summary} *(tool not registered" in index
    assert f"({local.uri}) — {local.summary}\n" in index


def test_render_index_lists_every_page_with_uri_and_summary() -> None:
    index = render_index(bundled_pages())

    assert index.startswith("# Basic Memory manual")
    assert "## Section 3 — MCP tools" in index
    for page in bundled_pages():
        assert f"- [{page.title}]({page.uri}) — {page.summary}" in index
