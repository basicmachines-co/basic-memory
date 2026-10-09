"""Recent activity prompts for Basic Memory MCP server.

These prompts help users see what has changed in their knowledge base recently.
"""

from textwrap import dedent
from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.recent_activity import recent_activity


@mcp.prompt(
    name="recent_activity",
    description=(
        "Get recent activity from one project: the given one, else the default project. "
        "Lists every project only when no default is set"
    ),
)
async def recent_activity_prompt(
    timeframe: Annotated[
        str,
        Field(description="How far back to look for activity (e.g. '1d', '1 week')"),
    ] = "7d",
    project: Annotated[
        Optional[str],
        Field(
            description=(
                "Project to get activity from (None uses the default project; every "
                "project is listed only when no default is set)"
            )
        ),
    ] = None,
) -> str:
    """Get recent activity from one project.

    This prompt helps you see what's changed recently in the knowledge base.
    With project=None it uses the active or default project, like the tool;
    it shows activity across all projects only when no project resolves.

    Args:
        timeframe: How far back to look for activity (e.g. '1d', '1 week')
        project: Project to get activity from (None uses the default project)

    Returns:
        Formatted summary of recent activity
    """
    timeframe = timeframe or "7d"
    logger.info(f"Getting recent activity, timeframe: {timeframe}, project: {project}")

    # Call the tool function - it returns a well-formatted string
    activity_summary = await recent_activity(project=project, timeframe=timeframe)

    # Build the prompt response
    # The tool already returns formatted markdown, so we use it directly
    # and add prompt-specific guidance
    # A bare call resolves to the default project; it reaches every project only
    # when no default is configured, so the header must not promise all projects.
    target = f"project '{project}'" if project else "the default project"

    prompt_guidance = dedent(f"""
        # Recent Activity Context

        This is a memory retrieval session showing recent activity from {target}.

        {activity_summary}

        ---

        ## Next Steps

        Based on this activity, you can:

        1. **Explore specific items** - Use `read_note("permalink")` to dive deeper into any item
        2. **Search for related content** - Use `search_notes("topic")` to find connected knowledge
        3. **Build context** - Use `build_context("memory://path")` to see relationships

        ## Capture Opportunity

        If this activity shows a pattern or development worth tracking, offer to record it
        with `write_note` (for example in an `insights` directory).
    """)

    return prompt_guidance
