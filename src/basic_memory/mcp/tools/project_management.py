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
from basic_memory.mcp.tools.utils import call_get, call_put, call_post, call_delete
from basic_memory.schemas import ProjectInfoResponse
from basic_memory.schemas.project_info import ProjectList, ProjectStatusResponse, ProjectInfoRequest


@mcp.tool()
async def list_projects(ctx: Context | None = None) -> str:
    """List all available projects with their status.

    Shows all Basic Memory projects that are available, indicating which one
    is currently active and which is the default.

    Returns:
        Formatted list of projects with status indicators

    Example:
        list_projects()
    """
    if ctx:  # pragma: no cover
        await ctx.info("Listing all available projects")

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
        switch_project("work-notes")
        switch_project("personal-journal")
    """
    if ctx:  # pragma: no cover
        await ctx.info(f"Switching to project: {project_name}")

    current_project = session.get_current_project()
    try:
        # Validate project exists by getting project list
        response = await call_get(client, "/projects/projects")
        project_list = ProjectList.model_validate(response.json())

        # Check if project exists
        project_exists = any(p.name == project_name for p in project_list.projects)
        if not project_exists:
            available_projects = [p.name for p in project_list.projects]
            return f"Error: Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"

        # Switch to the project
        session.set_current_project(project_name)
        current_project = session.get_current_project()
        project_config = get_project_config(current_project)

        # Get project info to show summary
        try:
            response = await call_get(client, f"{project_config.project_url}/project/info")
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
        session.set_current_project(current_project)
        raise e


@mcp.tool()
async def get_current_project(ctx: Context | None = None) -> str:
    """Show the currently active project and basic stats.

    Displays which project is currently active and provides basic information
    about it.

    Returns:
        Current project name and basic statistics

    Example:
        get_current_project()
    """
    if ctx:  # pragma: no cover
        await ctx.info("Getting current project information")

    current_project = session.get_current_project()
    project_config = get_project_config(current_project)
    result = f"Current project: {current_project}\n\n"

    # get project stats
    response = await call_get(client, f"{project_config.project_url}/project/info")
    project_info = ProjectInfoResponse.model_validate(response.json())

    result += f"• {project_info.statistics.total_entities} entities\n"
    result += f"• {project_info.statistics.total_observations} observations\n"
    result += f"• {project_info.statistics.total_relations} relations\n"

    default_project = session.get_default_project()
    if current_project != default_project:
        result += f"• Default project: {default_project}\n"

    return add_project_metadata(result, current_project)


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
        set_default_project("work-notes")
    """
    if ctx:  # pragma: no cover
        await ctx.info(f"Setting default project to: {project_name}")

    # Call API to set default project
    response = await call_put(client, f"/projects/{project_name}/default")
    status_response = ProjectStatusResponse.model_validate(response.json())

    result = f"✓ {status_response.message}\n\n"
    result += "Restart Basic Memory for this change to take effect:\n"
    result += "basic-memory mcp\n"

    if status_response.old_project:
        result += f"\nPrevious default: {status_response.old_project.name}\n"

    return add_project_metadata(result, session.get_current_project())


@mcp.tool()
async def create_project(
    project_name: str, project_path: str, set_default: bool = False, ctx: Context | None = None
) -> str:
    """Create a new Basic Memory project.

    Creates a new project with the specified name and path. The project directory
    will be created if it doesn't exist. Optionally sets the new project as default.

    Args:
        project_name: Name for the new project (must be unique)
        project_path: File system path where the project will be stored
        set_default: Whether to set this project as the default (optional, defaults to False)

    Returns:
        Confirmation message with project details

    Example:
        create_project("my-research", "~/Documents/research")
        create_project("work-notes", "/home/user/work", set_default=True)
    """
    if ctx:  # pragma: no cover
        await ctx.info(f"Creating project: {project_name} at {project_path}")

    # Create the project request
    project_request = ProjectInfoRequest(
        name=project_name, path=project_path, set_default=set_default
    )

    # Call API to create project
    response = await call_post(client, "/projects/projects", json=project_request.model_dump())
    status_response = ProjectStatusResponse.model_validate(response.json())

    result = f"✓ {status_response.message}\n\n"

    if status_response.new_project:
        result += "Project Details:\n"
        result += f"• Name: {status_response.new_project.name}\n"
        result += f"• Path: {status_response.new_project.path}\n"

        if set_default:
            result += "• Set as default project\n"

    result += "\nProject is now available for use.\n"

    # If project was set as default, update session
    if set_default:
        session.set_current_project(project_name)

    return add_project_metadata(result, session.get_current_project())


@mcp.tool()
async def delete_project(project_name: str, ctx: Context | None = None) -> str:
    """Delete a Basic Memory project.

    Removes a project from the configuration and database. This does NOT delete
    the actual files on disk - only removes the project from Basic Memory's
    configuration and database records.

    Args:
        project_name: Name of the project to delete

    Returns:
        Confirmation message about project deletion

    Example:
        delete_project("old-project")

    Warning:
        This action cannot be undone. The project will need to be re-added
        to access its content through Basic Memory again.
    """
    if ctx:  # pragma: no cover
        await ctx.info(f"Deleting project: {project_name}")

    current_project = session.get_current_project()

    # Check if trying to delete current project
    if project_name == current_project:
        raise ValueError(
            f"Cannot delete the currently active project '{project_name}'. Switch to a different project first."
        )

    # Get project info before deletion to validate it exists
    response = await call_get(client, "/projects/projects")
    project_list = ProjectList.model_validate(response.json())

    # Check if project exists
    project_exists = any(p.name == project_name for p in project_list.projects)
    if not project_exists:
        available_projects = [p.name for p in project_list.projects]
        raise ValueError(
            f"Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"
        )

    # Call API to delete project
    response = await call_delete(client, f"/projects/{project_name}")
    status_response = ProjectStatusResponse.model_validate(response.json())

    result = f"✓ {status_response.message}\n\n"

    if status_response.old_project:
        result += "Removed project details:\n"
        result += f"• Name: {status_response.old_project.name}\n"
        if hasattr(status_response.old_project, "path"):
            result += f"• Path: {status_response.old_project.path}\n"

    result += "Files remain on disk but project is no longer tracked by Basic Memory.\n"
    result += "Re-add the project to access its content again.\n"

    return add_project_metadata(result, session.get_current_project())
