"""Recent activity prompts for Basic Memory MCP server.

These prompts help users see what has changed in their knowledge base recently.
"""

from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from basic_memory.mcp.prompts.utils import format_prompt_context, PromptContext, PromptContextItem
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import GraphContext, ProjectActivitySummary
from basic_memory.schemas.search import SearchItemType


@mcp.prompt(
    name="recent_activity",
    description="Get recent activity from a specific project or across all projects",
)
async def recent_activity_prompt(
    timeframe: Annotated[
        TimeFrame,
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

    recent = await recent_activity.fn(
        project=project, timeframe=timeframe, type=[SearchItemType.ENTITY]
    )

    # Extract primary results from the hierarchical structure
    primary_results = []
    related_results = []

    if isinstance(recent, ProjectActivitySummary):
        # Discovery mode - extract results from all projects
        for _, project_activity in recent.projects.items():
            if project_activity.activity.results:
                # Take up to 2 primary results per project
                for item in project_activity.activity.results[:2]:
                    primary_results.append(item.primary_result)
                    # Add up to 1 related result per primary item
                    if item.related_results:
                        related_results.extend(item.related_results[:1])

        # Limit total results for readability
        primary_results = primary_results[:8]
        related_results = related_results[:6]

    elif isinstance(recent, GraphContext):
        # Project-specific mode - use existing logic
        if recent.results:
            # Take up to 5 primary results
            for item in recent.results[:5]:
                primary_results.append(item.primary_result)
                # Add up to 2 related results per primary item
                if item.related_results:
                    related_results.extend(item.related_results[:2])

    # Set topic based on mode
    if project:
        topic = f"Recent Activity in {project} ({timeframe})"
    else:
        topic = f"Recent Activity Across All Projects ({timeframe})"

    prompt_context = format_prompt_context(
        PromptContext(
            topic=topic,
            timeframe=timeframe,
            results=[
                PromptContextItem(
                    primary_results=primary_results,
                    related_results=related_results[:10],  # Limit total related results
                )
            ],
        )
    )

    # Add mode-specific suggestions
    first_title = "Recent Topic"
    if primary_results and len(primary_results) > 0:
        first_title = primary_results[0].title

    if project:
        # Project-specific suggestions
        capture_suggestions = f"""
    ## Opportunity to Capture Activity Summary

    Consider creating a summary note of recent activity in {project}:

    ```python
    await write_note(
        "{project}",
        title="Activity Summary {timeframe}",
        content='''
        # Activity Summary for {project} ({timeframe})

        ## Overview
        [Summary of key changes and developments in this project over this period]

        ## Key Updates
        [List main updates and their significance within this project]

        ## Observations
        - [trend] [Observation about patterns in recent activity]
        - [insight] [Connection between different activities]

        ## Relations
        - summarizes [[{first_title}]]
        - relates_to [[{project} Overview]]
        ''',
        folder="summaries"
    )
    ```

    Summarizing periodic activity helps create high-level insights and connections within the project.
    """
    else:
        # Discovery mode suggestions
        project_count = len(recent.projects) if isinstance(recent, ProjectActivitySummary) else 0
        most_active = (
            getattr(recent.summary, "most_active_project", "Unknown")
            if isinstance(recent, ProjectActivitySummary)
            else "Unknown"
        )

        capture_suggestions = f"""
    ## Cross-Project Activity Discovery

    Found activity across {project_count} projects. Most active: **{most_active}**

    Consider creating a cross-project summary:

    ```python
    await write_note(
        "{most_active if most_active != "Unknown" else "main"}",
        title="Cross-Project Activity Summary {timeframe}",
        content='''
        # Cross-Project Activity Summary ({timeframe})

        ## Overview
        Activity found across {project_count} projects, with {most_active} showing the most activity.

        ## Key Developments
        [Summarize important changes across all projects]

        ## Project Insights
        [Note patterns or connections between projects]

        ## Observations
        - [trend] [Cross-project patterns observed]
        - [insight] [Connections between different project activities]

        ## Relations
        - summarizes [[{first_title}]]
        - relates_to [[Project Portfolio Overview]]
        ''',
        folder="summaries"
    )
    ```

    Cross-project summaries help identify broader trends and project interconnections.
    """

    return prompt_context + capture_suggestions
