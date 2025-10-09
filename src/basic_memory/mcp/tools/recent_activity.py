"""Recent activity tool for Basic Memory MCP server."""

from typing import List, Union, Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.project_context import get_active_project, resolve_project_parameter
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import (
    GraphContext,
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
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Get recent activity for a specific project or across all projects.

    Project Resolution:
    The server resolves projects in this order:
    1. Single Project Mode - server constrained to one project, parameter ignored
    2. Explicit project parameter - specify which project to query
    3. Default project - server configured default if no project specified

    Discovery Mode:
    When no specific project can be resolved, returns activity across all projects
    to help discover available projects and their recent activity.

    Project Discovery (when project is unknown):
    1. Call list_memory_projects() to see available projects
    2. Or use this tool without project parameter to see cross-project activity
    3. Ask the user which project to focus on
    4. Remember their choice for the conversation

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
        project: Project name to query. Optional - server will resolve using the
                hierarchy above. If unknown, use list_memory_projects() to discover
                available projects.
        context: Optional FastMCP context for performance caching.

    Returns:
        Human-readable summary of recent activity. When no specific project is
        resolved, returns cross-project discovery information. When a specific
        project is resolved, returns detailed activity for that project.

    Examples:
        # Cross-project discovery mode
        recent_activity()
        recent_activity(timeframe="yesterday")

        # Project-specific activity
        recent_activity(project="work-docs", type="entity", timeframe="yesterday")
        recent_activity(project="research", type=["entity", "relation"], timeframe="today")
        recent_activity(project="notes", type="entity", depth=2, timeframe="2 weeks ago")

    Raises:
        ToolError: If project doesn't exist or type parameter contains invalid values

    Notes:
        - Higher depth values (>3) may impact performance with large result sets
        - For focused queries, consider using build_context with a specific URI
        - Max timeframe is 1 year in the past
    """
    async with get_client() as client:
        # Build common parameters for API calls
        params = {
            "page": 1,
            "page_size": 10,
            "max_related": 10,
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

        # Resolve project parameter using the three-tier hierarchy
        resolved_project = await resolve_project_parameter(project)

        if resolved_project is None:
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

            # Generate guidance for the assistant
            guidance_lines = ["\n" + "─" * 40]

            if most_active_project and most_active_count > 0:
                guidance_lines.extend(
                    [
                        f"Suggested project: '{most_active_project}' (most active with {most_active_count} items)",
                        f"Ask user: 'Should I use {most_active_project} for this task, or would you prefer a different project?'",
                    ]
                )
            elif active_projects > 0:
                # Has activity but no clear most active project
                active_project_names = [
                    name for name, activity in projects_activity.items() if activity.item_count > 0
                ]
                if len(active_project_names) == 1:
                    guidance_lines.extend(
                        [
                            f"Suggested project: '{active_project_names[0]}' (only active project)",
                            f"Ask user: 'Should I use {active_project_names[0]} for this task?'",
                        ]
                    )
                else:
                    guidance_lines.extend(
                        [
                            f"Multiple active projects found: {', '.join(active_project_names)}",
                            "Ask user: 'Which project should I use for this task?'",
                        ]
                    )
            else:
                # No recent activity
                guidance_lines.extend(
                    [
                        "No recent activity found in any project.",
                        "Consider: Ask which project to use or if they want to create a new one.",
                    ]
                )

            guidance_lines.extend(
                [
                    "",
                    "Session reminder: Remember their project choice throughout this conversation.",
                ]
            )

            guidance = "\n".join(guidance_lines)

            # Format discovery mode output
            return _format_discovery_output(projects_activity, summary, timeframe, guidance)

        else:
            # Project-Specific Mode: Get activity for specific project
            logger.info(
                f"Getting recent activity from project {resolved_project}: type={type}, depth={depth}, timeframe={timeframe}"
            )

            active_project = await get_active_project(client, resolved_project, context)
            project_url = active_project.project_url

            response = await call_get(
                client,
                f"{project_url}/memory/recent",
                params=params,
            )
            activity_data = GraphContext.model_validate(response.json())

            # Format project-specific mode output
            return _format_project_output(resolved_project, activity_data, timeframe, type)


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
            current_time = result.primary_result.created_at
            try:
                if last_activity is None or current_time > last_activity:
                    last_activity = current_time
            except TypeError:
                # Handle timezone comparison issues by skipping this comparison
                if last_activity is None:
                    last_activity = current_time

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


def _format_discovery_output(
    projects_activity: dict, summary: ActivityStats, timeframe: str, guidance: str
) -> str:
    """Format discovery mode output as human-readable text."""
    lines = [f"## Recent Activity Summary ({timeframe})"]

    # Most active project section
    if summary.most_active_project and summary.total_items > 0:
        most_active = projects_activity[summary.most_active_project]
        lines.append(
            f"\n**Most Active Project:** {summary.most_active_project} ({most_active.item_count} items)"
        )

        # Get latest activity from most active project
        if most_active.activity.results:
            latest = most_active.activity.results[0].primary_result
            title = latest.title if hasattr(latest, "title") and latest.title else "Recent activity"
            # Format relative time
            time_str = (
                _format_relative_time(latest.created_at) if latest.created_at else "unknown time"
            )
            lines.append(f"- 🔧 **Latest:** {title} ({time_str})")

        # Active folders
        if most_active.active_folders:
            folders = ", ".join(most_active.active_folders[:3])
            lines.append(f"- 📋 **Focus areas:** {folders}")

    # Other active projects
    other_active = [
        (name, activity)
        for name, activity in projects_activity.items()
        if activity.item_count > 0 and name != summary.most_active_project
    ]

    if other_active:
        lines.append("\n**Other Active Projects:**")
        for name, activity in sorted(other_active, key=lambda x: x[1].item_count, reverse=True)[:4]:
            lines.append(f"- **{name}** ({activity.item_count} items)")

    # Key developments - extract from recent entities
    key_items = []
    for name, activity in projects_activity.items():
        if activity.item_count > 0:
            for result in activity.activity.results[:3]:  # Top 3 from each active project
                if result.primary_result.type == "entity" and hasattr(
                    result.primary_result, "title"
                ):
                    title = result.primary_result.title
                    # Look for status indicators in titles
                    if any(word in title.lower() for word in ["complete", "fix", "test", "spec"]):
                        key_items.append(title)

    if key_items:
        lines.append("\n**Key Developments:**")
        for item in key_items[:5]:  # Show top 5
            status = "✅" if any(word in item.lower() for word in ["complete", "fix"]) else "🧪"
            lines.append(f"- {status} **{item}**")

    # Add summary stats
    lines.append(
        f"\n**Summary:** {summary.active_projects} active projects, {summary.total_items} recent items"
    )

    # Add guidance
    lines.append(guidance)

    return "\n".join(lines)


def _format_project_output(
    project_name: str,
    activity_data: GraphContext,
    timeframe: str,
    type_filter: Union[str, List[str]],
) -> str:
    """Format project-specific mode output as human-readable text."""
    lines = [f"## Recent Activity: {project_name} ({timeframe})"]

    if not activity_data.results:
        lines.append(f"\nNo recent activity found in '{project_name}' project.")
        return "\n".join(lines)

    # Group results by type
    entities = []
    relations = []
    observations = []

    for result in activity_data.results:
        if result.primary_result.type == "entity":
            entities.append(result.primary_result)
        elif result.primary_result.type == "relation":
            relations.append(result.primary_result)
        elif result.primary_result.type == "observation":
            observations.append(result.primary_result)

    # Show entities (notes/documents)
    if entities:
        lines.append(f"\n**📄 Recent Notes & Documents ({len(entities)}):**")
        for entity in entities[:5]:  # Show top 5
            title = entity.title if hasattr(entity, "title") and entity.title else "Untitled"
            # Get folder from file_path if available
            folder = ""
            if hasattr(entity, "file_path") and entity.file_path:
                folder_path = "/".join(entity.file_path.split("/")[:-1])
                if folder_path:
                    folder = f" ({folder_path})"
            lines.append(f"  • {title}{folder}")

    # Show observations (categorized insights)
    if observations:
        lines.append(f"\n**🔍 Recent Observations ({len(observations)}):**")
        # Group by category
        by_category = {}
        for obs in observations[:10]:  # Limit to recent ones
            category = (
                getattr(obs, "category", "general") if hasattr(obs, "category") else "general"
            )
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(obs)

        for category, obs_list in list(by_category.items())[:5]:  # Show top 5 categories
            lines.append(f"  **{category}:** {len(obs_list)} items")
            for obs in obs_list[:2]:  # Show 2 examples per category
                content = (
                    getattr(obs, "content", "No content")
                    if hasattr(obs, "content")
                    else "No content"
                )
                # Truncate at word boundary
                if len(content) > 80:
                    content = _truncate_at_word(content, 80)
                lines.append(f"    - {content}")

    # Show relations (connections)
    if relations:
        lines.append(f"\n**🔗 Recent Connections ({len(relations)}):**")
        for rel in relations[:5]:  # Show top 5
            rel_type = (
                getattr(rel, "relation_type", "relates_to")
                if hasattr(rel, "relation_type")
                else "relates_to"
            )
            from_entity = (
                getattr(rel, "from_entity", "Unknown") if hasattr(rel, "from_entity") else "Unknown"
            )
            to_entity = getattr(rel, "to_entity", None) if hasattr(rel, "to_entity") else None

            # Format as WikiLinks to show they're readable notes
            from_link = f"[[{from_entity}]]" if from_entity != "Unknown" else from_entity
            to_link = f"[[{to_entity}]]" if to_entity else "[Missing Link]"

            lines.append(f"  • {from_link} → {rel_type} → {to_link}")

    # Activity summary
    total = len(activity_data.results)
    lines.append(f"\n**Activity Summary:** {total} items found")
    if hasattr(activity_data, "metadata") and activity_data.metadata:
        if hasattr(activity_data.metadata, "total_results"):
            lines.append(f"Total available: {activity_data.metadata.total_results}")

    return "\n".join(lines)


def _format_relative_time(timestamp) -> str:
    """Format timestamp as relative time like '2 hours ago'."""
    try:
        from datetime import datetime, timezone
        from dateutil.relativedelta import relativedelta

        if isinstance(timestamp, str):
            # Parse ISO format timestamp
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        else:
            dt = timestamp

        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Use relativedelta for accurate time differences
        diff = relativedelta(now, dt)

        if diff.years > 0:
            return f"{diff.years} year{'s' if diff.years > 1 else ''} ago"
        elif diff.months > 0:
            return f"{diff.months} month{'s' if diff.months > 1 else ''} ago"
        elif diff.days > 0:
            if diff.days == 1:
                return "yesterday"
            elif diff.days < 7:
                return f"{diff.days} days ago"
            else:
                weeks = diff.days // 7
                return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        elif diff.hours > 0:
            return f"{diff.hours} hour{'s' if diff.hours > 1 else ''} ago"
        elif diff.minutes > 0:
            return f"{diff.minutes} minute{'s' if diff.minutes > 1 else ''} ago"
        else:
            return "just now"
    except Exception:
        return "recently"


def _truncate_at_word(text: str, max_length: int) -> str:
    """Truncate text at word boundary."""
    if len(text) <= max_length:
        return text

    # Find last space before max_length
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")

    if last_space > max_length * 0.7:  # Only truncate at word if we're not losing too much
        return text[:last_space] + "..."
    else:
        return text[: max_length - 3] + "..."
