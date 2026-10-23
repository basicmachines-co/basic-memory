"""bm_man: the bundled manual, with no project and no network (#1832)."""

import re
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from basic_memory.man import bundled_pages
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import bm_man


@pytest.mark.asyncio
async def test_no_arguments_is_the_index():
    index = await bm_man()

    assert isinstance(index, str)
    assert index.startswith("# Basic Memory manual")
    assert "[bm-search(3)](memory://man/bm-search(3))" in index


@pytest.mark.asyncio
async def test_index_json_lists_every_page():
    payload = await bm_man(output_format="json")

    assert isinstance(payload, dict)
    assert [row["page"] for row in payload["pages"]] == [page.title for page in bundled_pages()]
    assert all(row["registered"] for row in payload["pages"] if row["tool"] == "bm_search")


@pytest.mark.asyncio
@pytest.mark.parametrize("page", ["bm-search(3)", "bm_search", "bm-search", "3/bm-search"])
async def test_page_reads_any_spelling(page: str):
    text = await bm_man(page=page)

    assert isinstance(text, str)
    assert text.startswith("# bm-search(3)\n")
    assert "title: bm-search(3)" not in text  # frontmatter is not returned


@pytest.mark.asyncio
async def test_page_json_carries_content():
    payload = await bm_man(page="grep(1)", output_format="json")

    assert isinstance(payload, dict)
    assert payload["page"] == "grep(1)"
    assert payload["tool"] is None
    assert payload["content"].startswith("# grep(1)\n")


@pytest.mark.asyncio
async def test_query_lists_name_matches_first():
    text = await bm_man(query="manual")

    assert isinstance(text, str)
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    # bm-man(3)'s summary names the manual; a page that mentions it only in its body follows.
    assert lines.index("- bm-man(3) — read the Basic Memory manual") < len(lines) - 1


@pytest.mark.asyncio
async def test_query_with_no_match():
    text = await bm_man(query="zzz-no-such-word")
    payload = await bm_man(query="zzz-no-such-word", output_format="json")

    assert text == "No manual pages mention 'zzz-no-such-word'. bm_man() lists every page."
    assert payload == {"query": "zzz-no-such-word", "results": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("page", ["nope(3)", "docs/nope"])
async def test_missing_page_is_not_found(page: str):
    with pytest.raises(ToolError, match=rf"^NOT_FOUND: No manual page '{re.escape(page)}'"):
        await bm_man(page=page)


@pytest.mark.asyncio
async def test_page_and_query_together_are_refused():
    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: `page` reads one page"):
        await bm_man(page="grep(1)", query="grep")


async def call(arguments: dict[str, Any]) -> str:
    async with Client(mcp) as client:
        result = await client.call_tool("bm_man", arguments)
    return result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["topic", "name", "command", "tool"])
async def test_page_aliases_models_reach_for(alias: str):
    """Production man calls failed on each of these names (2026-10)."""
    assert (await call({alias: "grep(1)"})).startswith("# grep(1)\n")
