"""Recent activity tool for Basic Memory MCP server."""

from datetime import datetime
from typing import List, Union, Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.async_client import client
from basic_memory.mcp.project_context import get_active_project
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import (
    GraphContext,
    ProjectActivitySummary,
    ProjectActivity,
    ActivityStats,
)
from basic_memory.schemas.project_info import ProjectList, ProjectItem
from basic_memory.schemas.search import SearchItemType


@mcp.tool(
    description="""Get recent activity for a project or across all projects.

    Timeframe supports natural language formats like:
    - "2 days ago"
    - "last week"
    - "yesterday"
    - "today"
    - "3 weeks ago"
    Or standard formats like "7d"
    """,
)
async def recent_activity(
    type: Union[str, List[str]] = "",
    depth: int = 1,
    timeframe: TimeFrame = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
    project: Optional[str] = None,
    context: Context | None = None,
) -> Union[GraphContext, ProjectActivitySummary]:
    """Get recent activity for a specific project or across all projects.

    This tool works in two modes based on the project parameter:

    **Discovery Mode (project=None)**: Returns activity summary across all projects,
    enabling project discovery and cross-project activity overview.

    **Project-Specific Mode (project provided)**: Returns detailed activity for a
    specific project with filtering and graph traversal capabilities.

    Args:
        type: Filter by content type(s). Can be a string or list of strings.
            Valid options:
            - "entity" or ["entity"] for knowledge entities
            - "relation" or ["relation"] for connections between entities
            - "observation" or ["observation"] for notes and observations
            Multiple types can be combined: ["entity", "relation"]
            Case-insensitive: "ENTITY" and "entity" are treated the same.
            Default is an empty string, which returns all types.
        depth: How many relation hops to traverse (1-3 recommended)
        timeframe: Time window to search. Supports natural language:
            - Relative: "2 days ago", "last week", "yesterday"
            - Points in time: "2024-01-01", "January 1st"
            - Standard format: "7d", "24h"
        page: Page number of results to return (default: 1)
        page_size: Number of results to return per page (default: 10)
        max_related: Maximum number of related results to return (default: 10)
        project: Optional project name. If None, returns activity across all projects.
                 If provided, returns detailed activity for that specific project.
        context: Optional FastMCP context for performance caching.

    Returns:
        - ProjectActivitySummary: When project=None (discovery mode)
        - GraphContext: When project is specified (project-specific mode)

    Examples:
        # Discovery mode - see activity across all projects
        recent_activity()
        recent_activity(timeframe="yesterday")

        # Project-specific mode - detailed activity for one project
        recent_activity(project="my-project")
        recent_activity(project="work-docs", type="entity", timeframe="yesterday")
        recent_activity(project="research", type=["entity"], timeframe="yesterday")
        recent_activity(project="dev-notes", type=["relation", "observation"], timeframe="today")
        recent_activity(project="team-docs", type="entity", depth=2, timeframe="2 weeks ago")

    Raises:
        ToolError: If project doesn't exist or type parameter contains invalid values

    Notes:
        - Discovery mode enables project selection without session state
        - Higher depth values (>3) may impact performance with large result sets
        - For focused queries, consider using build_context with a specific URI
        - Max timeframe is 1 year in the past
    """
    # Build common parameters for API calls
    params = {
        "page": page,
        "page_size": page_size,
        "max_related": max_related,
    }
    if depth:
        params["depth"] = depth
    if timeframe:
        params["timeframe"] = timeframe  # pyright: ignore

    # Validate and convert type parameter
    if type:
        # Convert single string to list
        if isinstance(type, str):
            type_list = [type]
        else:
            type_list = type

        # Validate each type against SearchItemType enum
        validated_types = []
        for t in type_list:
            try:
                # Try to convert string to enum
                if isinstance(t, str):
                    validated_types.append(SearchItemType(t.lower()))
            except ValueError:
                valid_types = [t.value for t in SearchItemType]
                raise ValueError(f"Invalid type: {t}. Valid types are: {valid_types}")

        # Add validated types to params
        params["type"] = [t.value for t in validated_types]  # pyright: ignore

    if project is None:
        # Discovery Mode: Get activity across all projects
        logger.info(
            f"Getting recent activity across all projects: type={type}, depth={depth}, timeframe={timeframe}"
        )

        # Get list of all projects
        response = await call_get(client, "/projects/projects")
        project_list = ProjectList.model_validate(response.json())

        projects_activity = {}
        total_items = 0
        total_entities = 0
        total_relations = 0
        total_observations = 0
        most_active_project = None
        most_active_count = 0
        active_projects = 0

        # Query each project's activity
        for project_info in project_list.projects:
            project_activity = await _get_project_activity(client, project_info, params, depth)
            projects_activity[project_info.name] = project_activity

            # Aggregate stats
            item_count = project_activity.item_count
            if item_count > 0:
                active_projects += 1
                total_items += item_count

                # Count by type
                for result in project_activity.activity.results:
                    if result.primary_result.type == "entity":
                        total_entities += 1
                    elif result.primary_result.type == "relation":
                        total_relations += 1
                    elif result.primary_result.type == "observation":
                        total_observations += 1

                # Track most active project
                if item_count > most_active_count:
                    most_active_count = item_count
                    most_active_project = project_info.name

        # Build summary stats
        summary = ActivityStats(
            total_projects=len(project_list.projects),
            active_projects=active_projects,
            most_active_project=most_active_project,
            total_items=total_items,
            total_entities=total_entities,
            total_relations=total_relations,
            total_observations=total_observations,
        )

        return ProjectActivitySummary(
            projects=projects_activity,
            summary=summary,
            timeframe=str(timeframe),
            generated_at=datetime.now(),
        )

    else:
        # Project-Specific Mode: Get activity for specific project
        logger.info(
            f"Getting recent activity from project {project}: type={type}, depth={depth}, timeframe={timeframe}, page={page}, page_size={page_size}, max_related={max_related}"
        )

        active_project = await get_active_project(client, project, context)
        project_url = active_project.project_url

        response = await call_get(
            client,
            f"{project_url}/memory/recent",
            params=params,
        )
        return GraphContext.model_validate(response.json())


async def _get_project_activity(
    client, project_info: ProjectItem, params: dict, depth: int
) -> ProjectActivity:
    """Get activity data for a single project.

    Args:
        client: HTTP client for API calls
        project_info: Project information
        params: Query parameters for the activity request
        depth: Graph traversal depth

    Returns:
        ProjectActivity with activity data or empty activity on error
    """
    project_url = f"/{project_info.permalink}"
    activity_response = await call_get(
        client,
        f"{project_url}/memory/recent",
        params=params,
    )
    activity = GraphContext.model_validate(activity_response.json())

    # Extract last activity timestamp and active folders
    last_activity = None
    active_folders = set()

    for result in activity.results:
        if result.primary_result.created_at:
            if last_activity is None or result.primary_result.created_at > last_activity:
                last_activity = result.primary_result.created_at

        # Extract folder from file_path
        if hasattr(result.primary_result, "file_path") and result.primary_result.file_path:
            folder = "/".join(result.primary_result.file_path.split("/")[:-1])
            if folder:
                active_folders.add(folder)

    return ProjectActivity(
        project_name=project_info.name,
        project_path=project_info.path,
        activity=activity,
        item_count=len(activity.results),
        last_activity=last_activity,
        active_folders=list(active_folders)[:5],  # Limit to top 5 folders
    )
