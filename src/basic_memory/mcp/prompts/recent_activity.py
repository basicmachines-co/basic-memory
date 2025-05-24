"""Recent activity prompts for Basic Memory MCP server.

These prompts help users see what has changed in their knowledge base recently.
"""

from typing import Annotated

from loguru import logger
from pydantic import Field

from basic_memory.mcp.prompts.utils import format_prompt_context, PromptContext, PromptContextItem
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.search import SearchItemType


@mcp.prompt(
    name="Share Recent Activity",
    description="Get recent activity from across the knowledge base",
)
async def recent_activity_prompt(
    timeframe: Annotated[
        TimeFrame,
        Field(description="How far back to look for activity (e.g. '1d', '1 week')"),
    ] = "7d",
) -> str:
    """Get recent activity from across the knowledge base.

    This prompt helps you see what's changed recently in the knowledge base,
    showing new or updated documents and related information.

    Args:
        timeframe: How far back to look for activity (e.g. '1d', '1 week')

    Returns:
        Formatted summary of recent activity
    """
    logger.info(f"Getting recent activity, timeframe: {timeframe}")

    recent = await recent_activity(timeframe=timeframe, type=[SearchItemType.ENTITY])

    # Extract primary results from the hierarchical structure
    primary_results = []
    related_results = []

    if recent.results:
        # Take up to 5 primary results
        for item in recent.results[:5]:
            primary_results.append(item.primary_result)
            # Add up to 2 related results per primary item
            if item.related_results:
                related_results.extend(item.related_results[:2])

    prompt_context = format_prompt_context(
        PromptContext(
            topic=f"Recent Activity from ({timeframe})",
            timeframe=timeframe,
            results=[
                PromptContextItem(
                    primary_results=primary_results,
                    related_results=related_results[:10],  # Limit total related results
                )
            ],
        )
    )

    # Add suggestions for summarizing recent activity
    first_title = "Recent Topic"
    if primary_results and len(primary_results) > 0:
        first_title = primary_results[0].title

    capture_suggestions = f"""
    ## Opportunity to Capture Activity Summary
    
    Consider creating a summary note of recent activity:
    
    ```python
    await write_note(
        title="Activity Summary {timeframe}",
        content='''
        # Activity Summary for {timeframe}
        
        ## Overview
        [Summary of key changes and developments over this period]
        
        ## Key Updates
        [List main updates and their significance]
        
        ## Observations
        - [trend] [Observation about patterns in recent activity]
        - [insight] [Connection between different activities]
        
        ## Relations
        - summarizes [[{first_title}]]
        - relates_to [[Project Overview]]
        '''
    )
    ```
    
    Summarizing periodic activity helps create high-level insights and connections between topics.
    """

    return prompt_context + capture_suggestions
