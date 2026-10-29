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
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.tool_params import OutputFormat, Page, Pagination, ProjectParam, Since
from basic_memory.mcp.workspace_project_index import get_cached_active_workspace
from basic_memory.schemas.memory import (
    MAX_CONTEXT_DEPTH,
    MAX_CONTEXT_PAGE_SIZE,
    MAX_CONTEXT_RELATED_RESULTS,
    ContextResult,
    EntitySummary,
    GraphContext,
    ObservationSummary,
    RelationSummary,
    memory_url_path,
)


# --- Formatting ---


def _compact_observation(observation: ObservationSummary) -> ObservationSummary:
    """Navigate to the owning file without repeating content-derived labels."""
    return observation.model_copy(
        update={"title": observation.category, "permalink": observation.file_path}
    )


def _compact_context_labels(graph: GraphContext) -> GraphContext:
    # Observation titles and permalinks embed source prose. Use the category and
    # owning file for discovery; keep numeric and external IDs unchanged.
    # Compact requests skip entity observation lists, so only observations that
    # surface as primary or related results need relabeling.
    return graph.model_copy(
        update={
            "results": [
                result.model_copy(
                    update={
                        "primary_result": _compact_observation(result.primary_result)
                        if isinstance(result.primary_result, ObservationSummary)
                        else result.primary_result,
                        "related_results": [
                            _compact_observation(item)
                            if isinstance(item, ObservationSummary)
                            else item
                            for item in result.related_results
                        ],
                    }
                )
                for result in graph.results
            ]
        }
    )


def _format_entity_block(result: ContextResult, *, compact: bool = False) -> str:
    """Format a single context result as a markdown block.

    Each note carries its file path beside its permalink: a permalink need not
    carry the project prefix, and the path is the address bm_read takes in any session.
    """
    primary = result.primary_result
    lines = []

    # --- Header ---
    lines.append(f"## {primary.title}")
    if primary.permalink:
        lines.append(f"permalink: {primary.permalink}")
    lines.append(f"path: {primary.file_path}")
    # RelationSummary has no content field; Entity/Observation do
    if not compact and not isinstance(primary, RelationSummary) and primary.content:
        lines.append("")
        lines.append(primary.content)

    # --- Observations ---
    if result.observations and not compact:
        lines.append("")
        lines.append("### Observations")
        for obs in result.observations:
            lines.append(f"- [{obs.category}] {obs.content}")

    # --- Relations (from primary's related_results that are RelationSummary) ---
    relation_items: list[RelationSummary] = [
        r for r in result.related_results if isinstance(r, RelationSummary)
    ]
    if relation_items:
        lines.append("")
        lines.append("### Relations")
        primary_external_id = primary.external_id if isinstance(primary, EntitySummary) else None
        for rel in relation_items:
            # Unresolved forward references have no resolved entity yet; fall back
            # to the literal target text instead of rendering [[None]] (#955)
            target = rel.to_entity or rel.to_name
            # Trigger: the relation is incoming, or belongs to a neighbor at depth > 1.
            # Why: listed bare under this note, `relates_to [[This Note]]` reads as a
            #   self-link (#1717).
            # Outcome: relations this note owns stay bare; others name their source.
            is_outgoing = (
                primary_external_id is not None
                and rel.from_entity_external_id == primary_external_id
            )
            if is_outgoing or rel.from_entity is None:
                lines.append(f"- {rel.relation_type} [[{target}]]")
            else:
                lines.append(f"- [[{rel.from_entity}]] {rel.relation_type} [[{target}]]")

    # --- Related entities (non-relation related results) ---
    related_entities: list[EntitySummary | ObservationSummary] = [
        r for r in result.related_results if not isinstance(r, RelationSummary)
    ]
    if related_entities:
        lines.append("")
        lines.append("### Related")
        for item in related_entities:
            lines.append(f"- [[{item.title}]] ({item.file_path})")

    return "\n".join(lines)


def _format_context_markdown(graph: GraphContext, project: str, *, compact: bool = False) -> str:
    """Format GraphContext as compact markdown text.

    Produces a human-readable markdown representation that is much smaller
    than the equivalent JSON, suitable for LLM consumption when structured
    data isn't needed.
    """
    if not graph.results:
        uri = graph.metadata.uri or ""
        return f"No results found for '{uri}' in project '{project}'."

    parts = []

    # --- Title from first primary result ---
    first_title = graph.results[0].primary_result.title
    if len(graph.results) == 1:
        parts.append(f"# Context: {first_title}")
    else:
        uri = graph.metadata.uri or ""
        parts.append(f"# Context: {uri}")

    parts.append("")

    # --- Entity blocks separated by --- ---
    entity_blocks = [_format_entity_block(result, compact=compact) for result in graph.results]
    parts.append("\n\n---\n\n".join(entity_blocks))

    # --- Footer ---
    meta = graph.metadata
    primary_count = meta.primary_count or 0
    related_count = meta.related_count or 0
    parts.append("")
    parts.append("---")
    parts.append(
        f"*{primary_count} primary, {related_count} related"
        f" | depth={meta.depth} | project: {project}*"
    )

    return "\n".join(parts)


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
    *,
    project: ProjectParam,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Build graph context from a starting note or pattern."""
    if not identifier.strip():
        raise refusal(
            "INVALID_ARGUMENTS",
            "identifier cannot be empty or whitespace.",
            output_format=output_format,
        )
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
        text = _format_context_markdown(graph, target.project.name, compact=compact)
        next_call = Pagination(page=page, page_size=page_size, has_more=graph.has_more).next_call(
            "bm_context",
            {
                "identifier": identifier,
                "depth": depth,
                "since": since,
                "max_related": max_related,
                "compact": compact or None,
                "output_format": output_format,
                "project": project,
            },
        )
        if next_call is None:
            return text
        return f"{text}\n\nMore results available. Call {next_call} to continue."
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
