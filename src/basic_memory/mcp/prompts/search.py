"""Search prompts for Basic Memory MCP server.

These prompts help users search and explore their knowledge base.
"""

from textwrap import dedent
from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.search import search_notes as search_tool
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.search import SearchResponse


@mcp.prompt(
    name="Search Knowledge Base",
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
    logger.info(f"Searching knowledge base, query: {query}, timeframe: {timeframe}")

    search_results = await search_tool(query=query, after_date=timeframe)
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
            
            ## Opportunity to Capture Knowledge!
            
            This is an excellent opportunity to create new knowledge on this topic. Consider:
            
            ```python
            await write_note(
                title="{query.capitalize()}",
                content=f'''
                # {query.capitalize()}
                
                ## Overview
                [Summary of what we've discussed about {query}]
                
                ## Observations
                - [category] [First observation about {query}]
                - [category] [Second observation about {query}]
                
                ## Relations
                - relates_to [[Other Relevant Topic]]
                '''
            )
            ```
            
            ## Other Suggestions
            - Try a different search term
            - Broaden your search criteria
            - Check recent activity with `recent_activity(timeframe="1w")`
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
            - **Type**: {result.type.value}
            """)

        # Add creation date if available in metadata
        if result.metadata and "created_at" in result.metadata:
            created_at = result.metadata["created_at"]
            if hasattr(created_at, "strftime"):
                summary += (
                    f"- **Created**: {created_at.strftime('%Y-%m-%d %H:%M')}\n"  # pragma: no cover
                )
            elif isinstance(created_at, str):
                summary += f"- **Created**: {created_at}\n"

        # Add score and excerpt
        summary += f"- **Relevance Score**: {result.score:.2f}\n"

        # Add excerpt if available in metadata
        if result.content:
            summary += f"- **Excerpt**:\n{result.content}\n"

        # Add permalink for retrieving content
        if result.permalink:
            summary += dedent(f"""
                You can view this content with: `read_note("{result.permalink}")`
                Or explore its context with: `build_context("memory://{result.permalink}")`
                """)
        else:
            summary += dedent(f"""
                You can view this file with: `read_file("{result.file_path}")`
                """)  # pragma: no cover

    # Add next steps with strong write encouragement
    summary += dedent(f"""
        ## Next Steps
        
        You can:
        - Refine your search: `search_notes("{query} AND additional_term")`
        - Exclude terms: `search_notes("{query} NOT exclude_term")`
        - View more results: `search_notes("{query}", after_date=None)`
        - Check recent activity: `recent_activity()`
        
        ## Synthesize and Capture Knowledge
        
        Consider creating a new note that synthesizes what you've learned:
        
        ```python
        await write_note(
            title="Synthesis of {query.capitalize()} Information",
            content='''
            # Synthesis of {query.capitalize()} Information
            
            ## Overview
            [Synthesis of the search results and your conversation]
            
            ## Key Insights
            [Summary of main points learned from these results]
            
            ## Observations
            - [insight] [Important observation from search results]
            - [connection] [How this connects to other topics]
            
            ## Relations
            - relates_to [[{results.results[0].title if results.results else "Related Topic"}]]
            - extends [[Another Relevant Topic]]
            '''
        )
        ```
        
        Remember that capturing synthesized knowledge is one of the most valuable features of Basic Memory.
        """)

    return summary
