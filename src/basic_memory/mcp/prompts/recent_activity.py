"""Recent activity prompts for Basic Memory MCP server.

These prompts help users see what has changed in their knowledge base recently.
"""

from textwrap import dedent
from typing import Annotated, Optional

from loguru import logger
from fastmcp import Context
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.project_context import resolve_project_parameter
from basic_memory.mcp.tools.projects import bm_project


@mcp.prompt(
    name="recent_activity",
    description=(
        "Get recent activity from the given or configured default project. "
        "Discover projects first when neither resolves."
    ),
)
async def recent_activity_prompt(
    timeframe: Annotated[
        str,
        Field(description="How far back to look for activity (e.g. '1d', '1 week')"),
    ] = "7d",
    project: Annotated[
        Optional[str],
        Field(description="Project to get activity from (None uses the configured default)"),
    ] = None,
    context: Context | None = None,
) -> str:
    """Get recent activity from one project.

    This prompt helps you see what's changed recently in the knowledge base.
    With project=None the prompt resolves the default before calling the scoped tool.
    If no project resolves, it asks the caller to discover one first.

    Args:
        timeframe: How far back to look for activity (e.g. '1d', '1 week')
        project: Project to get activity from (None uses the default project)
        context: MCP request context

    Returns:
        Formatted summary of recent activity
    """
    timeframe = timeframe or "7d"
    logger.info(f"Getting recent activity, timeframe: {timeframe}, project: {project}")

    project = await resolve_project_parameter(project=project, context=context)
    if project is None:
        return (
            "Choose a project with bm_project(command='list'), then request recent "
            "activity with an explicit project name or external id."
        )

    # Call the tool function - it returns a well-formatted string
    activity_summary = await bm_project(
        command="recent", project=project, since=timeframe, context=context
    )

    # Build the prompt response
    # The tool already returns formatted markdown, so we use it directly
    # and add prompt-specific guidance
    scope = f" from project '{project}'"

    prompt_guidance = dedent(f"""
        # Recent Activity Context

        This is a memory retrieval session showing recent activity{scope}.

        {activity_summary}

        ---

        ## Next Steps

        Based on this activity, you can:

        1. **Explore specific items** - Use `bm_read("permalink", project={project!r})` to dive deeper into any item
        2. **Search for related content** - Use `bm_search("topic", project={project!r})` to find connected knowledge
        3. **Build context** - Use `bm_context("memory://path", project={project!r})` to see relationships

        ## Capture Opportunity

        If this activity shows a pattern or development worth tracking, offer to record it
        with `bm_write` (for example in an `insights` directory).
    """)

    return prompt_guidance
