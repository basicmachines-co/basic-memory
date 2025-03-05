"""Recent activity tool for Basic Memory MCP server."""

from typing import Optional, List

import logfire
from loguru import logger

from basic_memory.mcp.async_client import client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.memory import GraphContext
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.search import SearchItemType


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
@logfire.instrument(extract_args=False)
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
    logger.info(
        f"Getting recent activity from type={type}, depth={depth}, timeframe={timeframe}, page={page}, page_size={page_size}, max_related={max_related}"
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
