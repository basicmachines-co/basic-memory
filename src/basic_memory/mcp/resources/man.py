"""The manual as MCP resources.

``memory://man`` is the index (apropos); ``memory://man/<page>`` is one page. Every
bundled page is also registered as a concrete resource so clients that browse
``resources/list`` see each page with its summary, while the template underneath
accepts any spelling of a page reference — ``search-notes(3)``, ``3/search-notes``,
``search_notes`` — so an agent's first guess resolves.
"""

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
def manual_page(ref: str) -> str:
    try:
        page_ref = parse_page_ref(ref)
    except ValueError as error:
        raise ResourceError(f"{error}; read {MANUAL_INDEX_URI} for the index") from error
    page = find_page(page_ref)
    if page is None:
        raise ResourceError(
            f"No manual entry for {page_ref.display}; read {MANUAL_INDEX_URI} for the index"
        )
    return page.read()


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
