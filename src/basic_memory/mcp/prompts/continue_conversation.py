"""Session continuation prompts for Basic Memory MCP server.

These prompts help users continue conversations and work across sessions,
providing context from previous interactions to maintain continuity.
"""

from textwrap import dedent
from typing import Optional, List, Annotated

from loguru import logger
import logfire
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.build_context import build_context
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.mcp.tools.search import search
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import GraphContext
from basic_memory.schemas.search import SearchQuery, SearchItemType


@mcp.prompt(
    name="continue conversation",
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
            return format_continuation_context(topic, contexts[:3], timeframe)

        # If no topic, get recent activity
        recent = await recent_activity(timeframe=timeframe)
        return format_continuation_context("Recent Activity", [recent], timeframe)


def format_continuation_context(
    topic: str, contexts: List[GraphContext], timeframe: TimeFrame | None
) -> str:
    """Format continuation context into a helpful summary.

    Args:
        topic: The topic or focus of continuation
        contexts: List of context graphs
        timeframe: How far back to look for activity

    Returns:
        Formatted continuation summary
    """
    if not contexts or all(not context.primary_results for context in contexts):
        return dedent(f"""
            # Continuing conversation on: {topic}
            
            This is a memory retrieval session. 
            Please use the available basic-memory tools to gather relevant context before responding. 
            Start by executing one of the suggested commands below to retrieve content.
            
            I couldn't find any recent work specifically on this topic.
            
            ## Suggestions
            - Try a different search term
            - Check recent activity with `recent_activity(timeframe="1w")`
            - Start a new topic with `write_note(...)`
            """)

    # Start building our summary with header
    summary = dedent(f"""
        # Continuing conversation on: {topic}
        
        This is a memory retrieval session. 
        Please use the available basic-memory tools to gather relevant context before responding. 
        Start by executing one of the suggested commands below to retrieve content.
        
        Here's what I found about the previous conversation:
        """)

    # Track what we've added to avoid duplicates
    added_permalinks = set()
    sections = []

    # Process each context
    for context in contexts:
        # Add primary results
        for primary in context.primary_results:
            if hasattr(primary, "permalink") and primary.permalink not in added_permalinks:
                added_permalinks.add(primary.permalink)

                section = dedent(f"""
                    ## {primary.title}
                    - **Type**: {primary.type}
                    """)

                # Add creation date if available
                if hasattr(primary, "created_at"):
                    section += f"- **Created**: {primary.created_at.strftime('%Y-%m-%d %H:%M')}\n"

                # Add content snippet
                if hasattr(primary, "content") and primary.content:  # pyright: ignore
                    content = primary.content or ""  # pyright: ignore
                    if content:
                        section += f"- **Content Snippet**: {content}\n"

                section += dedent(f"""
                    
                    You can read this document with: `read_note("{primary.permalink}")`
                    """)

                # Add related documents if available
                related_by_type = {}
                if context.related_results:
                    for related in context.related_results:
                        if hasattr(related, "relation_type") and related.relation_type:  # pyright: ignore
                            if related.relation_type not in related_by_type:  # pyright: ignore
                                related_by_type[related.relation_type] = []  # pyright: ignore
                            related_by_type[related.relation_type].append(related)  # pyright: ignore

                if related_by_type:
                    section += dedent("""
                        ### Related Documents
                        """)
                    for rel_type, relations in related_by_type.items():
                        display_type = rel_type.replace("_", " ").title()
                        section += f"- **{display_type}**:\n"
                        for rel in relations[:3]:  # Limit to avoid overwhelming
                            if hasattr(rel, "to_entity") and rel.to_entity:
                                section += f"  - `{rel.to_entity}`\n"

                sections.append(section)

    # Add all sections
    summary += "\n".join(sections)

    # Add next steps
    next_steps = dedent(f"""
        ## Next Steps
        
        You can:
        - Explore more with: `search({{"text": "{topic}"}})`
        - See what's changed: `recent_activity(timeframe="{timeframe}")`
        """)

    # Add specific exploration based on what we found
    if added_permalinks:
        first_permalink = next(iter(added_permalinks))
        next_steps += dedent(f"""
            - Continue the conversation: `build_context("memory://{first_permalink}")`
            """)

    return summary + next_steps
