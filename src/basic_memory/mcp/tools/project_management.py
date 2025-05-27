"""Project management tools for Basic Memory MCP server.

These tools allow users to switch between projects, list available projects,
and manage project context during conversations.
"""

from fastmcp import Context
from loguru import logger

from basic_memory.config import get_project_config
from basic_memory.mcp.async_client import client
from basic_memory.mcp.project_session import session, add_project_metadata
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get, call_put
from basic_memory.schemas import ProjectInfoResponse
from basic_memory.schemas.project_info import ProjectList, ProjectStatusResponse


@mcp.tool()
async def list_projects(ctx: Context | None = None) -> str:
    """List all available projects with their status.

    Shows all Basic Memory projects that are available, indicating which one
    is currently active and which is the default.

    Returns:
        Formatted list of projects with status indicators

    Example:
        Available projects:
        • main (current, default)
        • work-notes
        • personal-journal
        • code-snippets
    """
    if ctx:  # pragma: no cover
        await ctx.info("Listing all available projects")

    try:
        # Get projects from API
        response = await call_get(client, "/projects/projects")
        project_list = ProjectList.model_validate(response.json())

        current = session.get_current_project()

        result = "Available projects:\n"

        for project in project_list.projects:
            indicators = []
            if project.name == current:
                indicators.append("current")
            if project.is_default:
                indicators.append("default")

            if indicators:
                result += f"• {project.name} ({', '.join(indicators)})\n"
            else:
                result += f"• {project.name}\n"

        return add_project_metadata(result, current)

    except Exception as e:
        logger.error(f"Error listing projects: {e}")
        return f"Error listing projects: {str(e)}"


@mcp.tool()
async def switch_project(project_name: str, ctx: Context | None = None) -> str:
    """Switch to a different project context.

    Changes the active project context for all subsequent tool calls.
    Shows a project summary after switching successfully.

    Args:
        project_name: Name of the project to switch to

    Returns:
        Confirmation message with project summary

    Example:
        ✓ Switched to work-notes project

        Project Summary:
        • 47 entities
        • 23 observations
        • 15 relations
    """
    if ctx:  # pragma: no cover
        await ctx.info(f"Switching to project: {project_name}")

    previous_project = session.get_current_project()
    try:
        # Validate project exists by getting project list
        base_url = get_project_config().project_url.replace(f"/{get_project_config().name}", "")
        response = await call_get(client, "/projects/projects")
        project_list = ProjectList.model_validate(response.json())

        # Check if project exists
        project_exists = any(p.name == project_name for p in project_list.projects)
        if not project_exists:
            available_projects = [p.name for p in project_list.projects]
            return f"Error: Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"

        # Switch to the project
        previous_project = session.get_current_project()
        session.set_current_project(project_name)

        # Get project info to show summary
        try:
            project_url = f"{base_url}/{project_name}"
            response = await call_get(client, f"{project_url}/project/info")
            project_info = ProjectInfoResponse.model_validate(response.json())

            result = f"✓ Switched to {project_name} project\n\n"
            result += "Project Summary:\n"
            result += f"• {project_info.statistics.total_entities} entities\n"
            result += f"• {project_info.statistics.total_observations} observations\n"
            result += f"• {project_info.statistics.total_relations} relations\n"

        except Exception as e:
            # If we can't get project info, still confirm the switch
            logger.warning(f"Could not get project info for {project_name}: {e}")
            result = f"✓ Switched to {project_name} project\n\n"
            result += "Project summary unavailable.\n"

        return add_project_metadata(result, project_name)

    except Exception as e:
        logger.error(f"Error switching to project {project_name}: {e}")
        # Revert to previous project on error
        session.set_current_project(previous_project)
        return f"Error switching to project '{project_name}': {str(e)}"  # pragma: no cover - bug: undefined var


@mcp.tool()
async def get_current_project(ctx: Context | None = None) -> str:
    """Show the currently active project and basic stats.

    Displays which project is currently active and provides basic information
    about it.

    Returns:
        Current project name and basic statistics

    Example:
        Current project: work-notes

        • 47 entities
        • 23 observations
        • 15 relations
        • Default project: main
    """
    if ctx:  # pragma: no cover
        await ctx.info("Getting current project information")

    try:
        current = session.get_current_project()
        result = f"Current project: {current}\n\n"

        # Get base URL for project API calls
        base_url = get_project_config().project_url.replace(f"/{get_project_config().name}", "")

        # Try to get project stats
        try:
            project_url = f"{base_url}/{current}"
            response = await call_get(client, f"{project_url}/project/info")
            project_info = ProjectInfoResponse.model_validate(response.json())

            result += f"• {project_info.statistics.total_entities} entities\n"
            result += f"• {project_info.statistics.total_observations} observations\n"
            result += f"• {project_info.statistics.total_relations} relations\n"

        except Exception as e:
            logger.warning(f"Could not get stats for current project: {e}")
            result += "• Statistics unavailable\n"

        # Get default project info
        try:
            response = await call_get(client, f"{base_url}/project/projects")
            project_list = ProjectList.model_validate(response.json())
            default = project_list.default_project

            if current != default:
                result += f"• Default project: {default}\n"
        except Exception:
            pass

        return add_project_metadata(result, current)

    except Exception as e:
        logger.error(f"Error getting current project: {e}")
        return f"Error getting current project: {str(e)}"


@mcp.tool()
async def set_default_project(project_name: str, ctx: Context | None = None) -> str:
    """Set default project in config. Requires restart to take effect.

    Updates the configuration to use a different default project. This change
    only takes effect after restarting the Basic Memory server.

    Args:
        project_name: Name of the project to set as default

    Returns:
        Confirmation message about config update

    Example:
        ✓ Updated default project to 'work-notes' in configuration

        Restart Basic Memory for this change to take effect:
        basic-memory mcp
    """
    if ctx:  # pragma: no cover
        await ctx.info(f"Setting default project to: {project_name}")

    try:
        # Call API to set default project
        response = await call_put(client, f"/projects/{project_name}/default")
        status_response = ProjectStatusResponse.model_validate(response.json())

        result = f"✓ {status_response.message}\n\n"
        result += "Restart Basic Memory for this change to take effect:\n"
        result += "basic-memory mcp\n"

        if status_response.old_project:
            result += f"\nPrevious default: {status_response.old_project.name}\n"

        return add_project_metadata(result, session.get_current_project())

    except Exception as e:
        logger.error(f"Error setting default project: {e}")
        return f"Error setting default project '{project_name}': {str(e)}"
