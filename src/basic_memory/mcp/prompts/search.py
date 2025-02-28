"""Search prompts for Basic Memory MCP server.

These prompts help users search and explore their knowledge base.
"""

from textwrap import dedent
from typing import Annotated, Optional

from loguru import logger
import logfire
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.search import search as search_tool
from basic_memory.schemas.search import SearchQuery, SearchResponse
from basic_memory.schemas.base import TimeFrame


@mcp.prompt(
    name="search",
    description="Search across all content in basic-memory",
)
async def search_prompt(
    query: str,
    timeframe: Annotated[
        Optional[TimeFrame],
        Field(description="How far back to search (e.g. '1d', '1 week')"),
    ] = None,
) -> str:
    """Search across all content in basic-memory.

    This prompt helps search for content in the knowledge base and
    provides helpful context about the results.

    Args:
        query: The search text to look for
        timeframe: Optional timeframe to limit results (e.g. '1d', '1 week')

    Returns:
        Formatted search results with context
    """
    with logfire.span("Searching knowledge base", query=query, timeframe=timeframe):  # pyright: ignore
        logger.info(f"Searching knowledge base, query: {query}, timeframe: {timeframe}")

        search_results = await search_tool(SearchQuery(text=query, after_date=timeframe))
        return format_search_results(query, search_results, timeframe)


def format_search_results(
    query: str, results: SearchResponse, timeframe: Optional[TimeFrame] = None
) -> str:
    """Format search results into a helpful summary.

    Args:
        query: The search query
        results: Search results object
        timeframe: How far back results were searched

    Returns:
        Formatted search results summary
    """
    if not results.results:
        return dedent(f"""
            # Search Results for: "{query}"
            
            I couldn't find any results for this query.
            
            ## Suggestions
            - Try a different search term
            - Broaden your search criteria
            - Check recent activity with `recent_activity(timeframe="1w")`
            - Create new content with `write_note(...)`
            """)

    # Start building our summary with header
    time_info = f" (after {timeframe})" if timeframe else ""
    summary = dedent(f"""
        # Search Results for: "{query}"{time_info}
        
        This is a memory search session.
        Please use the available basic-memory tools to gather relevant context before responding.
        I found {len(results.results)} results that match your query.
        
        Here are the most relevant results:
        """)

    # Add each search result
    for i, result in enumerate(results.results[:5]):  # Limit to top 5 results
        summary += dedent(f"""
            ## {i + 1}. {result.title}
            - **Type**: {result.type}
            """)

        # Add creation date if available in metadata
        if hasattr(result, "metadata") and result.metadata and "created_at" in result.metadata:
            created_at = result.metadata["created_at"]
            if hasattr(created_at, "strftime"):
                summary += f"- **Created**: {created_at.strftime('%Y-%m-%d %H:%M')}\n"
            elif isinstance(created_at, str):
                summary += f"- **Created**: {created_at}\n"

        # Add score and excerpt
        summary += f"- **Relevance Score**: {result.score:.2f}\n"
        # Add excerpt if available in metadata
        if hasattr(result, "metadata") and result.metadata and "excerpt" in result.metadata:
            summary += f"- **Excerpt**: {result.metadata['excerpt']}\n"

        # Add permalink for retrieving content
        if hasattr(result, "permalink") and result.permalink:
            summary += dedent(f"""
                
                You can view this content with: `read_note("{result.permalink}")`
                Or explore its context with: `build_context("memory://{result.permalink}")`
                """)

    # Add next steps
    summary += dedent(f"""
        ## Next Steps
        
        You can:
        - Refine your search: `search("{query} AND additional_term")`
        - Exclude terms: `search("{query} NOT exclude_term")`
        - View more results: `search("{query}", after_date=None)`
        - Check recent activity: `recent_activity()`
        """)

    return summary
