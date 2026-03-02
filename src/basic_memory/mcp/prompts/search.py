"""Search prompts for Basic Memory MCP server.

These prompts help users search and explore their knowledge base.
"""

from textwrap import dedent
from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.search import search_notes


@mcp.prompt(
    name="search_knowledge_base",
    description="Search across all content in basic-memory",
)
async def search_prompt(
    query: str,
    timeframe: Annotated[
        Optional[str],
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
    logger.info(f"Searching knowledge base, query: {query}, timeframe: {timeframe}")

    # Use json format to get structured data for result counting and formatting
    result = await search_notes(query=query, after_date=timeframe, output_format="json")

    # Format the tool output into a prompt with guidance
    if isinstance(result, dict):
        results = result.get("results", [])
        result_count = len(results)
        result_text = _format_search_results(results, query)
    else:
        # Error string from search tool
        result_count = 0
        result_text = str(result)

    return dedent(f"""
        # Search Results: "{query}"

        This is a memory retrieval session showing search results.

        {result_text}

        ---

        ## Next Steps

        Based on these {result_count} results, you can:

        1. **Read a specific note** - Use `read_note("permalink")` to see full content
        2. **Build context** - Use `build_context("memory://path")` to see relationships
        3. **Refine search** - Use `search_notes("refined query")` to narrow results
        4. **Check recent activity** - Use `recent_activity(timeframe="7d")` for recent changes
    """)


def _format_search_results(results: list[dict], query: str) -> str:
    """Format search result dicts into readable markdown."""
    if not results:
        return f"No results found for '{query}'."

    lines = [f"Found {len(results)} results:\n"]

    for item in results:
        title = item.get("title", "Untitled")
        permalink = item.get("permalink", "")
        score = item.get("score")
        score_text = f" (score: {score:.2f})" if score else ""

        lines.append(f"- **{title}**{score_text}")
        if permalink:
            lines.append(f"  permalink: {permalink}")
        content = item.get("content")
        if content:
            # Truncate content snippet
            content = content[:200] + "..." if len(content) > 200 else content
            lines.append(f"  {content}")
        lines.append("")

    return "\n".join(lines)
