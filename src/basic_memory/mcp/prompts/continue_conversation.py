"""Session continuation prompts for Basic Memory MCP server.

These prompts help users continue conversations and work across sessions,
providing context from previous interactions to maintain continuity.
"""

from textwrap import dedent
from typing import Any, Annotated, Optional

from fastmcp import Context
from loguru import logger
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.project_context import resolve_project_parameter
from basic_memory.mcp.tools.projects import bm_project
from basic_memory.mcp.tools.ranked_search import bm_search


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
    project: Annotated[
        str | None, Field(description="Project to search; omitted uses the configured default")
    ] = None,
    context: Context | None = None,
) -> str:
    """Continue a previous conversation or work session.

    This prompt helps you pick up where you left off by finding recent context
    about a specific topic or showing general recent activity.

    Args:
        topic: Topic or keyword to search for (optional)
        timeframe: How far back to look for activity
        project: Explicit project, or the configured default
        context: MCP request context

    Returns:
        Context from previous sessions on this topic
    """
    logger.info(f"Continuing session, topic: {topic}, timeframe: {timeframe}")

    # Prompts choose their default at the entrypoint; tool calls always name it.
    project = await resolve_project_parameter(project=project, context=context)
    if project is None:
        return (
            "Choose a project with bm_project(command='list'), then continue this "
            "conversation with an explicit project name or external id."
        )

    if topic:
        # Use json format to get structured data for result counting and branching
        result = await bm_search(
            query=topic, since=timeframe, project=project, context=context, output_format="json"
        )

        if isinstance(result, dict):
            results = result.get("results", [])
            context_text = _format_continuation_results(results, topic)
            result_count = len(results)
        else:
            # Error string
            context_text = str(result)
            result_count = 0
    else:
        # No topic — show recent activity
        effective_timeframe = timeframe or "7d"
        activity_text = await bm_project(
            command="recent", since=effective_timeframe, project=project, context=context
        )
        context_text = str(activity_text)
        result_count = -1  # Signals we used recent_activity

    target = f"'{topic}'" if topic else "recent activity"

    prompt = dedent(f"""
        # Continuing conversation on: {target}

        This is a memory retrieval session.

        The notes below came from Basic Memory. Open a note with `bm_read` or its linked notes
        with `bm_context` when a snippet is not enough to respond.

        {context_text}

        ---

        ## Next Steps
    """)

    prompt += f"\nPass `project={project!r}` on follow-up note and search calls.\n"

    if topic and result_count > 0:
        prompt += dedent(f"""
            Found {result_count} results related to '{topic}'.

            1. **Read full content** - Use `bm_read("permalink")` to dive into specific notes
            2. **Build context** - Use `bm_context("memory://path")` to see relationships
            3. **Search deeper** - Use `bm_search("{topic}")` with different filters

            If this conversation produces a decision or insight worth keeping, offer to save it
            with `bm_write`.
        """)
    elif topic:
        prompt += dedent(f"""
            No previous context found for '{topic}'.

            Try `bm_search` with related terms or check `bm_project(command="recent", project={project!r})`. If the user wants this
            topic tracked, offer to start a note with `bm_write`.
        """)
    else:
        prompt += dedent("""
            1. **Explore specific items** - Use `bm_read("permalink")` to dive deeper
            2. **Search for topics** - Use `bm_search("topic")` to find specific content
            3. **Build context** - Use `bm_context("memory://path")` to see relationships
        """)

    return prompt


def _format_continuation_results(results: list[dict[str, Any]], topic: str) -> str:
    """Format search result dicts for conversation continuation context."""
    if not results:
        return f"No previous context found for '{topic}'."

    lines = [f"## Previous Context for '{topic}'\n"]

    for item in results:
        title = item.get("title", "Untitled")
        permalink = item.get("permalink", "")

        lines.append(f"### {title}")
        if permalink:
            lines.append(f"permalink: {permalink}")
            lines.append(f'Read with: `bm_read("{permalink}")`')
        content = item.get("content")
        if content:
            content = content[:300] + "..." if len(content) > 300 else content
            lines.append(f"\n{content}")
        lines.append("")

    return "\n".join(lines)
