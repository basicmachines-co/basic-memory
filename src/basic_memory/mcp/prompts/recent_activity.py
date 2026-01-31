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
    description="Get recent activity from a specific project or across all projects",
)
async def recent_activity_prompt(
    timeframe: Annotated[
        str,
        Field(description="How far back to look for activity (e.g. '1d', '1 week')"),
    ] = "7d",
    project: Annotated[
        Optional[str],
        Field(
            description="Specific project to get activity from (None for discovery across all projects)"
        ),
    ] = None,
) -> str:
    """Get recent activity from a specific project or across all projects.

    This prompt helps you see what's changed recently in the knowledge base.
    In discovery mode (project=None), it shows activity across all projects.
    In project-specific mode, it shows detailed activity for one project.

    Args:
        timeframe: How far back to look for activity (e.g. '1d', '1 week')
        project: Specific project to get activity from (None for discovery across all projects)

    Returns:
        Formatted summary of recent activity
    """
    logger.info(f"Getting recent activity, timeframe: {timeframe}, project: {project}")

    # Call the tool function - it returns a well-formatted string
    # Pass type as string values (not enum) to match the tool's expected input
    activity_summary = await recent_activity.fn(project=project, timeframe=timeframe, type="entity")

    # Build the prompt response
    # The tool already returns formatted markdown, so we use it directly
    # and add prompt-specific guidance
    target = project if project else "all projects"

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

        If you notice patterns or insights from this activity, consider documenting them:

        ```python
        write_note(
            title="Activity Insights - {timeframe}",
            content='''
            # Activity Insights

            ## Patterns Observed
            - [trend] [Pattern you noticed in the activity]

            ## Key Developments
            - [insight] [Important development worth tracking]

            ## Relations
            - summarizes [[Recent Work]]
            ''',
            folder="insights",
            project="{project or "default"}"
        )
        ```
    """)

    return prompt_guidance
