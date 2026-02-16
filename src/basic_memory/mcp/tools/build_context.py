"""Build context tool for Basic Memory MCP server."""

from typing import Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.project_context import get_project_client, resolve_project_and_path
from basic_memory.mcp.server import mcp
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import (
    ContextResult,
    EntitySummary,
    GraphContext,
    MemoryUrl,
    ObservationSummary,
    RelationSummary,
)

# --- Fields to strip from each model (redundant with parent entity) ---

_OBSERVATION_STRIP = {
    "observation_id",
    "entity_id",
    "entity_external_id",
    "title",
    "file_path",
    "created_at",
}
_RELATION_STRIP = {
    "relation_id",
    "entity_id",
    "from_entity_id",
    "from_entity_external_id",
    "to_entity_id",
    "to_entity_external_id",
    "title",
    "file_path",
    "created_at",
}
_ENTITY_STRIP = {"entity_id", "created_at"}
_METADATA_STRIP = {"total_results", "generated_at"}


def _slim_summary(summary: EntitySummary | RelationSummary | ObservationSummary) -> dict:
    """Strip redundant fields from a summary model based on its type."""
    if isinstance(summary, ObservationSummary):
        strip = _OBSERVATION_STRIP
    elif isinstance(summary, RelationSummary):
        strip = _RELATION_STRIP
    else:
        strip = _ENTITY_STRIP

    data = summary.model_dump()
    for key in strip:
        data.pop(key, None)
    return data


def _slim_context(graph: GraphContext) -> dict:
    """Transform GraphContext into a slimmed dict, stripping redundant fields.

    Reduces payload size ~40% by removing fields on nested objects that
    duplicate information already present on the parent entity (IDs,
    timestamps, file paths).
    """
    slimmed_results = []
    for result in graph.results:
        slimmed_results.append(
            {
                "primary_result": _slim_summary(result.primary_result),
                "observations": [_slim_summary(obs) for obs in result.observations],
                "related_results": [_slim_summary(rel) for rel in result.related_results],
            }
        )

    metadata = graph.metadata.model_dump()
    for key in _METADATA_STRIP:
        metadata.pop(key, None)

    return {
        "results": slimmed_results,
        "metadata": metadata,
        "page": graph.page,
        "page_size": graph.page_size,
    }


def _format_entity_block(result: ContextResult) -> str:
    """Format a single context result as a markdown block."""
    primary = result.primary_result
    lines = []

    # --- Header ---
    lines.append(f"## {primary.title}")
    if primary.permalink:
        lines.append(f"permalink: {primary.permalink}")
    # RelationSummary has no content field; Entity/Observation do
    if not isinstance(primary, RelationSummary) and primary.content:
        lines.append("")
        lines.append(primary.content)

    # --- Observations ---
    if result.observations:
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
        for rel in relation_items:
            lines.append(f"- {rel.relation_type} [[{rel.to_entity}]]")

    # --- Related entities (non-relation related results) ---
    related_entities: list[EntitySummary | ObservationSummary] = [
        r for r in result.related_results if not isinstance(r, RelationSummary)
    ]
    if related_entities:
        lines.append("")
        lines.append("### Related")
        for item in related_entities:
            permalink = item.permalink if item.permalink else ""
            lines.append(f"- [[{item.title}]] ({permalink})")

    return "\n".join(lines)


def _format_context_markdown(graph: GraphContext, project: str) -> str:
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
    entity_blocks = [_format_entity_block(result) for result in graph.results]
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
    description="""Build context from a memory:// URI to continue conversations naturally.

    Use this to follow up on previous discussions or explore related topics.

    Memory URL Format:
    - Use paths like "folder/note" or "memory://folder/note"
    - Pattern matching: "folder/*" matches all notes in folder
    - Valid characters: letters, numbers, hyphens, underscores, forward slashes
    - Avoid: double slashes (//), angle brackets (<>), quotes, pipes (|)
    - Examples: "specs/search", "projects/basic-memory", "notes/*"

    Timeframes support natural language like:
    - "2 days ago", "last week", "today", "3 months ago"
    - Or standard formats like "7d", "24h"

    Format options:
    - "json" (default): Slimmed JSON with redundant fields removed
    - "markdown": Compact markdown text for LLM consumption
    """,
)
async def build_context(
    url: MemoryUrl,
    project: Optional[str] = None,
    depth: str | int | None = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
    format: str = "json",
    context: Context | None = None,
) -> dict | str:
    """Get context needed to continue a discussion within a specific project.

    This tool enables natural continuation of discussions by loading relevant context
    from memory:// URIs. It uses pattern matching to find relevant content and builds
    a rich context graph of related information.

    Project Resolution:
    Server resolves projects using a unified priority chain (same in local and cloud modes):
    Single Project Mode → project parameter → default project.
    Uses default project automatically. Specify `project` parameter to target a different project.

    Args:
        project: Project name to build context from. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        url: memory:// URI pointing to discussion content (e.g. memory://specs/search)
        depth: How many relation hops to traverse (1-3 recommended for performance)
        timeframe: How far back to look. Supports natural language like "2 days ago", "last week"
        page: Page number of results to return (default: 1)
        page_size: Number of results to return per page (default: 10)
        max_related: Maximum number of related results to return (default: 10)
        format: Response format - "json" for slimmed JSON dict, "markdown" for compact text
        context: Optional FastMCP context for performance caching.

    Returns:
        dict (format="json"): Slimmed JSON with redundant fields removed
        str (format="markdown"): Compact markdown representation

    Examples:
        # Continue a specific discussion
        build_context("my-project", "memory://specs/search")

        # Get deeper context about a component
        build_context("work-docs", "memory://components/memory-service", depth=2)

        # Get markdown output for compact context
        build_context("research", "memory://specs/search", format="markdown")

    Raises:
        ToolError: If project doesn't exist or depth parameter is invalid
    """
    logger.info(f"Building context from {url} in project {project}")

    # Convert string depth to integer if needed
    if isinstance(depth, str):
        try:
            depth = int(depth)
        except ValueError:
            from mcp.server.fastmcp.exceptions import ToolError

            raise ToolError(f"Invalid depth parameter: '{depth}' is not a valid integer")

    # URL is already validated and normalized by MemoryUrl type annotation

    async with get_project_client(project, context) as (client, active_project):
        # Resolve memory:// identifier with project-prefix awareness
        _, resolved_path, _ = await resolve_project_and_path(client, url, project, context)

        # Import here to avoid circular import
        from basic_memory.mcp.clients import MemoryClient

        # Use typed MemoryClient for API calls
        memory_client = MemoryClient(client, active_project.external_id)
        graph = await memory_client.build_context(
            resolved_path,
            depth=depth or 1,
            timeframe=timeframe,
            page=page,
            page_size=page_size,
            max_related=max_related,
        )

        if format == "markdown":
            return _format_context_markdown(graph, active_project.name)

        return _slim_context(graph)
