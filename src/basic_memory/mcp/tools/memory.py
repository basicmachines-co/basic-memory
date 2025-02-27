"""Discussion context tools for Basic Memory MCP server."""

from typing import Optional, Literal, List, Annotated

from loguru import logger
import logfire
from pydantic import Field

from basic_memory.mcp.async_client import client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.memory import (
    GraphContext,
    MemoryUrl,
    memory_url_path,
    normalize_memory_url,
)
from basic_memory.schemas.base import TimeFrame


@mcp.prompt(
    name="build_context",
    description="Build context from a memory:// URI",
)
async def build_context_prompt(url: MemoryUrl) -> str:
    """Build context from a memory:// URI."""
    context = await build_context(url=url)
    return _format_context_summary(f"# Knowledge Context: {url}", context)


@mcp.prompt(
    name="recent_activity",
    description="Get recent activity from across the knowledge base",
)
async def recent_activity_prompt(
    instructions: Annotated[
        str, Field(description="How to process or focus on the recent activity")
    ] = None,
) -> str:
    """Get recent activity from across the knowledge base."""
    timeframe = "2d"
    results = await recent_activity(timeframe=timeframe)
    
    header = f"{instructions}\n #Recent Activity: {timeframe}"
    return _format_context_summary(header, results)


def _format_context_summary(header: str, context: GraphContext) -> str:
    """Format GraphContext as a helpful markdown summary.

    This creates a user-friendly markdown response that explains the context
    and provides guidance on how to explore further.
    """
    summary = []

    # Extract URI for reference
    uri = context.metadata.uri or "a/permalink-value"

    # Add header
    summary.append(f"{header}")
    summary.append("")

    # Primary document section
    if context.primary_results:
        summary.append(f"## Primary Documents ({len(context.primary_results)})")

        for primary in context.primary_results:
            summary.append(f"### {primary.title}")
            summary.append(f"- **Type**: {primary.type}")
            summary.append(f"- **Path**: {primary.file_path}")
            summary.append(f"- **Created**: {primary.created_at.strftime('%Y-%m-%d %H:%M')}")
            summary.append("")
            summary.append(
                f'To view this document\'s content: `read_note("{primary.permalink}")` or `read_note("{primary.title}")` '
            )
            summary.append("")
    else:
        summary.append("\nNo primary documents found.")

    # Related documents section
    if context.related_results:
        summary.append(f"## Related Documents ({len(context.related_results)})")

        # Group by relation type for better organization
        relation_types = {}
        for rel in context.related_results:
            if hasattr(rel, "relation_type"):
                rel_type = rel.relation_type
                if rel_type not in relation_types:
                    relation_types[rel_type] = []
                relation_types[rel_type].append(rel)

        # Display relations grouped by type
        for rel_type, relations in relation_types.items():
            summary.append(f"### {rel_type.replace('_', ' ').title()} ({len(relations)})")

            for rel in relations:
                if hasattr(rel, "to_id") and rel.to_id:
                    summary.append(f"- **{rel.to_id}**")
                    summary.append(f'  - View document: `read_note("{rel.to_id}")` ')
                    summary.append(
                        f'  - Explore connections: `build_context("memory://{rel.to_id}")` '
                    )
                else:
                    summary.append(f"- **Unresolved relation**: {rel.permalink}")
            summary.append("")

    # Next steps section
    summary.append("## Next Steps")
    summary.append("Here are some ways to explore further:")

    search_term = uri.split("/")[-1]
    summary.append(f'- **Search related topics**: `search({{"text": "{search_term}"}})`')

    summary.append('- **Check recent changes**: `recent_activity(timeframe="3 days")`')
    summary.append(f'- **Explore all relations**: `build_context("memory://{uri}/*")`')

    # Tips section
    summary.append("")
    summary.append("## Tips")
    summary.append(
        f'- For more specific context, increase depth: `build_context("memory://{uri}", depth=2)`'
    )
    summary.append(
        "- You can follow specific relation types using patterns like: `memory://document/relation-type/*`"
    )
    summary.append("- Look for connected documents by checking relations between them")

    return "\n".join(summary)


@mcp.tool(
    description="""Build context from a memory:// URI to continue conversations naturally.
    
    Use this to follow up on previous discussions or explore related topics.
    Timeframes support natural language like:
    - "2 days ago"
    - "last week" 
    - "today"
    - "3 months ago"
    Or standard formats like "7d", "24h"
    """,
)
async def build_context(
    url: MemoryUrl,
    depth: Optional[int] = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
) -> GraphContext:
    """Get context needed to continue a discussion.

    This tool enables natural continuation of discussions by loading relevant context
    from memory:// URIs. It uses pattern matching to find relevant content and builds
    a rich context graph of related information.

    Args:
        url: memory:// URI pointing to discussion content (e.g. memory://specs/search)
        depth: How many relation hops to traverse (1-3 recommended for performance)
        timeframe: How far back to look. Supports natural language like "2 days ago", "last week"
        page: Page number of results to return (default: 1)
        page_size: Number of results to return per page (default: 10)
        max_related: Maximum number of related results to return (default: 10)

    Returns:
        GraphContext containing:
            - primary_results: Content matching the memory:// URI
            - related_results: Connected content via relations
            - metadata: Context building details

    Examples:
        # Continue a specific discussion
        build_context("memory://specs/search")

        # Get deeper context about a component
        build_context("memory://components/memory-service", depth=2)

        # Look at recent changes to a specification
        build_context("memory://specs/document-format", timeframe="today")

        # Research the history of a feature
        build_context("memory://features/knowledge-graph", timeframe="3 months ago")
    """
    with logfire.span("Building context", url=url, depth=depth, timeframe=timeframe):  # pyright: ignore [reportGeneralTypeIssues]
        logger.info(f"Building context from {url}")
        url = normalize_memory_url(url)
        response = await call_get(
            client,
            f"/memory/{memory_url_path(url)}",
            params={
                "depth": depth,
                "timeframe": timeframe,
                "page": page,
                "page_size": page_size,
                "max_related": max_related,
            },
        )
        return GraphContext.model_validate(response.json())


@mcp.tool(
    description="""Get recent activity from across the knowledge base.
    
    Timeframe supports natural language formats like:
    - "2 days ago"  
    - "last week"
    - "yesterday" 
    - "today"
    - "3 weeks ago"
    Or standard formats like "7d"
    """,
)
async def recent_activity(
    type: List[Literal["entity", "observation", "relation"]] = [],
    depth: Optional[int] = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
) -> GraphContext:
    """Get recent activity across the knowledge base.

    Args:
        type: Filter by content type(s). Valid options:
            - ["entity"] for knowledge entities
            - ["relation"] for connections between entities
            - ["observation"] for notes and observations
            Multiple types can be combined: ["entity", "relation"]
        depth: How many relation hops to traverse (1-3 recommended)
        timeframe: Time window to search. Supports natural language:
            - Relative: "2 days ago", "last week", "yesterday"
            - Points in time: "2024-01-01", "January 1st"
            - Standard format: "7d", "24h"
        page: Page number of results to return (default: 1)
        page_size: Number of results to return per page (default: 10)
        max_related: Maximum number of related results to return (default: 10)

    Returns:
        GraphContext containing:
            - primary_results: Latest activities matching the filters
            - related_results: Connected content via relations
            - metadata: Query details and statistics

    Examples:
        # Get all entities for the last 10 days (default)
        recent_activity()

        # Get all entities from yesterday
        recent_activity(type=["entity"], timeframe="yesterday")

        # Get recent relations and observations
        recent_activity(type=["relation", "observation"], timeframe="today")

        # Look back further with more context
        recent_activity(type=["entity"], depth=2, timeframe="2 weeks ago")

    Notes:
        - Higher depth values (>3) may impact performance with large result sets
        - For focused queries, consider using build_context with a specific URI
        - Max timeframe is 1 year in the past
    """
    with logfire.span("Getting recent activity", type=type, depth=depth, timeframe=timeframe):  # pyright: ignore [reportGeneralTypeIssues]
        logger.info(
            f"Getting recent activity from {type}, depth={depth}, timeframe={timeframe}, page={page}, page_size={page_size}, max_related={max_related}"
        )
        params = {
            "depth": depth,
            "timeframe": timeframe,
            "page": page,
            "page_size": page_size,
            "max_related": max_related,
        }
        if type:
            params["type"] = type

        response = await call_get(
            client,
            "/memory/recent",
            params=params,
        )
        return GraphContext.model_validate(response.json())
