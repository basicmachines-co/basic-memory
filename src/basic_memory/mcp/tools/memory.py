"""Discussion context tools for Basic Memory MCP server."""

from typing import Optional, List

from loguru import logger
import logfire

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
from basic_memory.schemas.search import SearchItemType


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
    type: Optional[List[SearchItemType]] = None,
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
            "page": page,
            "page_size": page_size,
            "max_related": max_related,
        }
        if depth:
            params["depth"] = depth
        if timeframe:
            params["timeframe"] = timeframe  # pyright: ignore

        # send enum values if we have an enum, else send string value
        if type:
            params["type"] = [  # pyright: ignore
                type.value if isinstance(type, SearchItemType) else type for type in type
            ]

        response = await call_get(
            client,
            "/memory/recent",
            params=params,
        )
        return GraphContext.model_validate(response.json())
