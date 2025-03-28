"""Session continuation prompts for Basic Memory MCP server.

These prompts help users continue conversations and work across sessions,
providing context from previous interactions to maintain continuity.
"""

from textwrap import dedent
from typing import Optional, Annotated

from loguru import logger
from pydantic import Field

from basic_memory.mcp.prompts.utils import format_prompt_context, PromptContext, PromptContextItem
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.build_context import build_context
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.mcp.tools.search import search_notes
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import GraphContext
from basic_memory.schemas.search import SearchQuery, SearchItemType


@mcp.prompt(
    name="Continue Conversation",
    description="Continue a previous conversation",
)
async def continue_conversation(
    topic: Annotated[Optional[str], Field(description="Topic or keyword to search for")] = None,
    timeframe: Annotated[
        Optional[TimeFrame],
        Field(description="How far back to look for activity (e.g. '1d', '1 week')"),
    ] = None,
) -> str:
    """Continue a previous conversation or work session.

    This prompt helps you pick up where you left off by finding recent context
    about a specific topic or showing general recent activity.

    Args:
        topic: Topic or keyword to search for (optional)
        timeframe: How far back to look for activity

    Returns:
        Context from previous sessions on this topic
    """
    logger.info(f"Continuing session, topic: {topic}, timeframe: {timeframe}")

    # If topic provided, search for it
    if topic:
        search_results = await search_notes(
            SearchQuery(text=topic, after_date=timeframe, types=[SearchItemType.ENTITY])
        )

        # Build context from results
        contexts = []
        for result in search_results.results:
            if hasattr(result, "permalink") and result.permalink:
                context: GraphContext = await build_context(f"memory://{result.permalink}")
                if context.primary_results:
                    contexts.append(
                        PromptContextItem(
                            primary_results=context.primary_results[:1],  # pyright: ignore
                            related_results=context.related_results[:3],  # pyright: ignore
                        )
                    )

        # get context for the top 3 results
        prompt_context = format_prompt_context(
            PromptContext(topic=topic, timeframe=timeframe, results=contexts)  # pyright: ignore
        )

    else:
        # If no topic, get recent activity
        timeframe = timeframe or "7d"
        recent: GraphContext = await recent_activity(
            timeframe=timeframe, type=[SearchItemType.ENTITY]
        )
        prompt_context = format_prompt_context(
            PromptContext(
                topic=f"Recent Activity from ({timeframe})",
                timeframe=timeframe,
                results=[
                    PromptContextItem(
                        primary_results=recent.primary_results[:5],  # pyright: ignore
                        related_results=recent.related_results[:2],  # pyright: ignore
                    )
                ],
            )
        )

    # Add next steps with strong encouragement to write
    next_steps = dedent(f"""
        ## Next Steps

        You can:
        - Explore more with: `search_notes({{"text": "{topic}"}})`
        - See what's changed: `recent_activity(timeframe="{timeframe or "7d"}")`
        - **Record new learnings or decisions from this conversation:** `write_note(title="[Create a meaningful title]", content="[Content with observations and relations]")`
        
        ## Knowledge Capture Recommendation
        
        As you continue this conversation, **actively look for opportunities to:**
        1. Record key information, decisions, or insights that emerge
        2. Link new knowledge to existing topics 
        3. Suggest capturing important context when appropriate
        4. Create forward references to topics that might be created later
        
        Remember that capturing knowledge during conversations is one of the most valuable aspects of Basic Memory.
        """)

    return prompt_context + next_steps
