"""Session continuation prompts for Basic Memory MCP server.

These prompts help users continue conversations and work across sessions,
providing context from previous interactions to maintain continuity.
"""

from textwrap import dedent
from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.mcp.tools.search import search_notes
from basic_memory.schemas.search import SearchResponse


@mcp.prompt(
    name="continue_conversation",
    description="Continue a previous conversation",
)
async def continue_conversation(
    topic: Annotated[Optional[str], Field(description="Topic or keyword to search for")] = None,
    timeframe: Annotated[
        Optional[str],
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

    if topic:
        # Search for the topic using the search tool directly
        result = await search_notes(query=topic, after_date=timeframe)

        if isinstance(result, SearchResponse):
            context_text = _format_continuation_results(result, topic)
            result_count = len(result.results)
        elif isinstance(result, dict):
            results = result.get("results", [])
            context_text = str(result)
            result_count = len(results)
        else:
            # Error string
            context_text = str(result)
            result_count = 0
    else:
        # No topic — show recent activity
        effective_timeframe = timeframe or "7d"
        activity_text = await recent_activity(timeframe=effective_timeframe)
        context_text = str(activity_text)
        result_count = -1  # Signals we used recent_activity

    target = f"'{topic}'" if topic else "recent activity"

    prompt = dedent(f"""
        # Continuing conversation on: {target}

        This is a memory retrieval session.

        Please use the available basic-memory tools to gather relevant context before responding.
        Start by executing one of the suggested commands below to retrieve content.

        {context_text}

        ---

        ## Next Steps
    """)

    if topic and result_count > 0:
        prompt += dedent(f"""
            Found {result_count} results related to '{topic}'.

            1. **Read full content** - Use `read_note("permalink")` to dive into specific notes
            2. **Build context** - Use `build_context("memory://path")` to see relationships
            3. **Search deeper** - Use `search_notes("{topic}")` with different filters

            > **Knowledge Capture:** As you continue this conversation, actively look for
            > opportunities to record new information, decisions, or insights using `write_note()`.
        """)
    elif topic:
        prompt += dedent(f"""
            No previous context found for '{topic}'.

            This is an opportunity to start documenting this topic:

            1. **Create a new note** - Use `write_note(title="{topic}", content="...")` to start
            2. **Search with variations** - Try `search_notes("{topic}")` with different terms
            3. **Check recent activity** - Use `recent_activity(timeframe="7d")` to see what's new
        """)
    else:
        prompt += dedent("""
            1. **Explore specific items** - Use `read_note("permalink")` to dive deeper
            2. **Search for topics** - Use `search_notes("topic")` to find specific content
            3. **Build context** - Use `build_context("memory://path")` to see relationships
        """)

    return prompt


def _format_continuation_results(result: SearchResponse, topic: str) -> str:
    """Format search results for conversation continuation context."""
    if not result.results:
        return f"No previous context found for '{topic}'."

    lines = [f"## Previous Context for '{topic}'\n"]

    for item in result.results:
        title = item.title or "Untitled"
        permalink = item.permalink or ""

        lines.append(f"### {title}")
        if permalink:
            lines.append(f"permalink: {permalink}")
            lines.append(f'Read with: `read_note("{permalink}")`')
        if item.content:
            content = item.content[:300] + "..." if len(item.content) > 300 else item.content
            lines.append(f"\n{content}")
        lines.append("")

    return "\n".join(lines)
