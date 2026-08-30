"""The manual as MCP resources.

``memory://man`` is the index (apropos); ``memory://man/<page>`` is one page. Every
bundled page is also registered as a concrete resource so clients that browse
``resources/list`` see each page with its summary, while the template underneath
accepts any spelling of a page reference — ``search-notes(3)``, ``3/search-notes``,
``search_notes`` — so an agent's first guess resolves.
"""

from fastmcp import Context
from fastmcp.exceptions import ResourceError
from fastmcp.resources import FileResource
from pydantic import AnyUrl

from basic_memory.man import bundled_pages, find_page, parse_page_ref, render_index
from basic_memory.mcp.server import mcp

MANUAL_INDEX_URI = "memory://man"
MANUAL_PAGE_TEMPLATE = "memory://man/{ref*}"


@mcp.resource(
    uri=MANUAL_INDEX_URI,
    name="manual",
    description="Index of the Basic Memory manual: every page with a one-line summary.",
    mime_type="text/markdown",
)
async def manual_index() -> str:
    # Mark pages whose tool this server does not register (hosted-only tools on a
    # local server, and vice versa) so an agent does not call a tool that is not there.
    tools = await mcp.list_tools(run_middleware=False)
    return render_index(bundled_pages(), frozenset(tool.name for tool in tools))


@mcp.resource(
    uri=MANUAL_PAGE_TEMPLATE,
    name="manual page",
    description=(
        "One manual page, e.g. memory://man/search-notes(3). Section-3 pages document "
        "each MCP tool with parameters, verified examples, and gotchas. Any common "
        "spelling of the page name resolves: search-notes(3), 3/search-notes, search_notes."
    ),
    mime_type="text/markdown",
)
async def manual_page(ref: str, context: Context | None = None) -> str:
    try:
        page_ref = parse_page_ref(ref)
    except ValueError as error:
        page = None
        miss = ResourceError(f"{error}; read {MANUAL_INDEX_URI} for the index")
    else:
        page = find_page(page_ref)
        miss = ResourceError(
            f"No manual entry for {page_ref.display}; read {MANUAL_INDEX_URI} for the index"
        )
    if page is not None:
        return page.read()

    # This template registers first and wins ties for memory://man/... over the
    # notes template, and nothing reserves `man` as a project name — so when no
    # page matches, the URI may be a note in a project really named man.
    # Deferred import: notes.py imports this module.
    from basic_memory.mcp.resources.notes import NoteNotFoundError, read_note_markdown

    try:
        return await read_note_markdown(f"man/{ref}", context)
    except NoteNotFoundError:
        # Neither a page nor a note — the manual's hint is the useful one; an
        # operational note failure keeps its own cause instead.
        raise miss from None


# Concrete resources are what clients list; the template only answers reads.
for _page in bundled_pages():
    mcp.add_resource(
        FileResource(
            uri=AnyUrl(_page.uri),
            name=_page.title,
            description=_page.summary,
            mime_type="text/markdown",
            path=_page.path,
        )
    )
