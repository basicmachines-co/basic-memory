"""bm_context: follow the knowledge graph from a note or pattern.

The starting point routes through the shared resolver, so an explicit `project` is
never re-resolved from the identifier's first segment, and '<project>/path' and
memory:// URLs work as everywhere else. Output defaults to JSON because the result
is a graph.
"""

from typing import Annotated, Any

from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.config import ConfigManager
from basic_memory.mcp.clients import MemoryClient
from basic_memory.mcp.note_targets import open_project_target
from basic_memory.mcp.project_context_identifiers import canonical_memory_path_for_active_route
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import OutputFormat, Page, ProjectParam, Since
from basic_memory.mcp.workspace_project_index import get_cached_active_workspace
from basic_memory.mcp.tools.build_context import (
    _compact_context_labels,
    _format_context_markdown,
)
from basic_memory.schemas.memory import (
    MAX_CONTEXT_DEPTH,
    MAX_CONTEXT_PAGE_SIZE,
    MAX_CONTEXT_RELATED_RESULTS,
    memory_url_path,
)


@mcp.tool(
    name="bm_context",
    title="Build context",
    description=(
        "Follow the Basic Memory knowledge graph from a note: the note, its observations, "
        "and the notes its relations reach, up to `depth` hops (1-5). `identifier` is a "
        "path, permalink, memory:// URL, or a pattern such as 'specs/*'. `since` limits "
        "related results to recent changes. `compact` returns the graph without note "
        "bodies; read chosen notes with bm_read."
    ),
    tags={"navigation"},
    annotations={
        "title": "Build context",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_context(
    identifier: Annotated[
        str,
        Field(
            min_length=1,
            validation_alias=AliasChoices("identifier", "url"),
            description=(
                "Where to start: a note path, permalink, memory:// URL, or a pattern such "
                "as 'specs/*'."
            ),
        ),
    ],
    depth: Annotated[
        int, Field(ge=1, le=MAX_CONTEXT_DEPTH, description="Relation hops to follow (1-5).")
    ] = 1,
    since: Since = "7d",
    max_related: Annotated[
        int,
        Field(
            ge=0,
            le=MAX_CONTEXT_RELATED_RESULTS,
            description="Most related results to return (0-100).",
        ),
    ] = 10,
    compact: Annotated[
        bool, Field(description="Omit note bodies and observations; keep the graph shape.")
    ] = False,
    page: Page = 1,
    # The context API pages at most 50 primary results, below the shared limit of 100.
    page_size: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_CONTEXT_PAGE_SIZE,
            validation_alias=AliasChoices("page_size", "limit", "per_page"),
            description=f"Primary results per page (1-{MAX_CONTEXT_PAGE_SIZE}).",
        ),
    ] = 10,
    output_format: OutputFormat = "json",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Build graph context from a starting note or pattern."""
    async with open_project_target(
        identifier, project, context, kind="identifier", output_format=output_format
    ) as target:
        # Trigger: every starting point, exact path or pattern.
        # Why: the context API matches permalinks in their index form, which carries
        #      the project prefix when permalinks_include_project is on (and the
        #      workspace form in Cloud). Routing has already stripped a project
        #      prefix the caller wrote, and a relative pattern such as 'specs/*'
        #      matches no index-form permalink.
        # Outcome: the path is put back into index form, as memory:// reads are.
        index_path = canonical_memory_path_for_active_route(
            target.project,
            memory_url_path(target.route.path),
            include_project=ConfigManager().config.permalinks_include_project,
            cached_workspace=await get_cached_active_workspace(context),
        )
        graph = await MemoryClient(target.client, target.project.external_id).build_context(
            index_path,
            depth=depth,
            timeframe=since,
            page=page,
            page_size=page_size,
            max_related=max_related,
            # Compact discovery never shows observations, so the server skips loading
            # them, which is the costly part of shaping the graph (#1571).
            include_observations=not compact,
        )

    # Paths come back in the spelling the caller used to reach the project, so any
    # item in the graph can be passed straight to bm_read.
    graph = target.paths.graph(graph)
    if compact:
        graph = _compact_context_labels(graph)
    if output_format == "text":
        return _format_context_markdown(graph, target.project.name, compact=compact)
    if compact:
        # The server returned no observations, so the list is dropped rather than
        # shown empty; bodies are left for an explicit bm_read.
        return graph.model_dump(
            exclude={
                "results": {
                    "__all__": {
                        "primary_result": {"content"},
                        "observations": True,
                        "related_results": {"__all__": {"content"}},
                    }
                }
            }
        )
    return graph.model_dump()
