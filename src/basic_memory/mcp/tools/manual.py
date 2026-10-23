"""bm_man: read the Basic Memory manual.

The manual is the pages bundled in the package (basic_memory.man), so every
install reads the same pages with no project and no network. Section 3 documents
the MCP tools, section 1 the `bm` shell commands, section 7 the concepts.
"""

from typing import Annotated, Any

from pydantic import AliasChoices, Field

from basic_memory.man import (
    ManPage,
    bundled_pages,
    find_page,
    parse_page_ref,
    render_index,
    search_pages,
)
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import OutputFormat


@mcp.tool(
    name="bm_man",
    title="Manual",
    description=(
        "Read the Basic Memory manual. With no arguments, the index of every page. "
        "`page` reads one page: 'bm-search(3)' for an MCP tool, 'grep(1)' for a shell "
        "command; a tool name such as 'bm_search' works too. `query` lists the pages "
        "that mention every word given, like `man -k`."
    ),
    tags={"navigation"},
    annotations={
        "title": "Manual",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_man(
    page: Annotated[
        str | None,
        Field(
            min_length=1,
            # Models reach for these names; production calls failed on each (2026-10).
            validation_alias=AliasChoices("page", "topic", "name", "command", "tool"),
            description="The page to read, e.g. 'bm-search(3)', 'grep(1)' or 'bm_search'.",
        ),
    ] = None,
    query: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("query", "search"),
            description="Words to find in the manual; lists the pages that mention them all.",
        ),
    ] = None,
    output_format: OutputFormat = "text",
) -> str | dict[str, Any]:
    """Read one manual page, search the manual, or list every page."""
    if page is not None and query is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "`page` reads one page and `query` searches the manual; give one.",
            output_format=output_format,
        )

    # --- One page ---
    if page is not None:
        found = _find(page)
        if found is None:
            raise refusal(
                "NOT_FOUND",
                f"No manual page '{page}'.",
                try_instead=f"bm_man(query='{page}'), or bm_man() for the index",
                output_format=output_format,
            )
        if output_format == "json":
            return {**_entry(found), "content": found.body()}
        return found.body()

    # --- apropos ---
    if query is not None:
        matches = search_pages(query)
        if output_format == "json":
            return {"query": query, "results": [_entry(match) for match in matches]}
        if not matches:
            return f"No manual pages mention '{query}'. bm_man() lists every page."
        lines = [f"- {match.title} — {match.summary}" for match in matches]
        return "\n".join([f"Manual pages for '{query}':", "", *lines])

    # --- Index ---
    # The bundle serves the local and the hosted server, whose tools differ, so the
    # index marks pages for tools this server does not register.
    tools = frozenset(tool.name for tool in await mcp.list_tools(run_middleware=False))
    if output_format == "json":
        return {
            "pages": [
                {**_entry(entry), "registered": entry.tool is None or entry.tool in tools}
                for entry in bundled_pages()
            ]
        }
    return render_index(bundled_pages(), tools)


def _find(page: str) -> ManPage | None:
    """The bundled page a reference names; a spelling that names no page finds none."""
    try:
        return find_page(parse_page_ref(page))
    except ValueError:
        return None


def _entry(page: ManPage) -> dict[str, Any]:
    return {"page": page.title, "summary": page.summary, "tool": page.tool}
