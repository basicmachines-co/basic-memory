"""Session continuation prompts for Basic Memory MCP server.

These prompts help users continue conversations and work across sessions,
providing context from previous interactions to maintain continuity.
"""
from textwrap import dedent
from typing import Optional, Annotated

from loguru import logger
import logfire
from pydantic import Field

from basic_memory.mcp.prompts.utils import format_prompt_context
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.build_context import build_context
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.mcp.tools.search import search
from basic_memory.schemas.base import TimeFrame
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
    with logfire.span("Continuing session", topic=topic, timeframe=timeframe):  # pyright: ignore
        logger.info(f"Continuing session, topic: {topic}, timeframe: {timeframe}")

        # If topic provided, search for it
        if topic:
            search_results = await search(
                SearchQuery(text=topic, after_date=timeframe, types=[SearchItemType.ENTITY])
            )

            # Build context from results
            contexts = []
            for result in search_results.results:
                if hasattr(result, "permalink") and result.permalink:
                    context = await build_context(f"memory://{result.permalink}")
                    contexts.append(context)

            # get context for the top 3 results
            return format_prompt_context(topic, contexts[:3], timeframe)

        # If no topic, get recent activity
        timeframe = timeframe or "7d"
        recent = await recent_activity(timeframe=timeframe)
        prompt_context = format_prompt_context(f"Recent Activity from ({timeframe})", [recent], timeframe)

        # Add next steps
        next_steps = dedent(f"""
            ## Next Steps

            You can:
            - Explore more with: `search({{"text": "{topic}"}})`
            - See what's changed: `recent_activity(timeframe="{timeframe}")`
            """)

        return prompt_context + next_steps


