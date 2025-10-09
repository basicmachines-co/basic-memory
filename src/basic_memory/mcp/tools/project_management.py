"""Project management tools for Basic Memory MCP server.

These tools allow users to switch between projects, list available projects,
and manage project context during conversations.
"""

import os
from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get, call_post, call_delete
from basic_memory.schemas.project_info import (
    ProjectList,
    ProjectStatusResponse,
    ProjectInfoRequest,
)
from basic_memory.utils import generate_permalink


@mcp.tool("list_memory_projects")
async def list_memory_projects(context: Context | None = None) -> str:
    """List all available projects with their status.

    Shows all Basic Memory projects that are available for MCP operations.
    Use this tool to discover projects when you need to know which project to use.

    Use this tool:
    - At conversation start when project is unknown
    - When user asks about available projects
    - Before any operation requiring a project

    After calling:
    - Ask user which project to use
    - Remember their choice for the session

    Returns:
        Formatted list of projects with session management guidance

    Example:
        list_memory_projects()
    """
    async with get_client() as client:
        if context:  # pragma: no cover
            await context.info("Listing all available projects")

        # Check if server is constrained to a specific project
        constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")

        # Get projects from API
        response = await call_get(client, "/projects/projects")
        project_list = ProjectList.model_validate(response.json())

        if constrained_project:
            result = f"Project: {constrained_project}\n\n"
            result += "Note: This MCP server is constrained to a single project.\n"
            result += "All operations will automatically use this project."
        else:
            # Show all projects with session guidance
            result = "Available projects:\n"

            for project in project_list.projects:
                result += f"• {project.name}\n"

            result += "\n" + "─" * 40 + "\n"
            result += "Next: Ask which project to use for this session.\n"
            result += "Example: 'Which project should I use for this task?'\n\n"
            result += "Session reminder: Track the selected project for all subsequent operations in this conversation.\n"
            result += "The user can say 'switch to [project]' to change projects."

        return result


@mcp.tool("create_memory_project")
async def create_memory_project(
    project_name: str, project_path: str, set_default: bool = False, context: Context | None = None
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
        create_memory_project("my-research", "~/Documents/research")
        create_memory_project("work-notes", "/home/user/work", set_default=True)
    """
    async with get_client() as client:
        # Check if server is constrained to a specific project
        constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
        if constrained_project:
            return f'# Error\n\nProject creation disabled - MCP server is constrained to project \'{constrained_project}\'.\nUse the CLI to create projects: `basic-memory project add "{project_name}" "{project_path}"`'

        if context:  # pragma: no cover
            await context.info(f"Creating project: {project_name} at {project_path}")

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

        result += "\nProject is now available for use in tool calls.\n"
        result += f"Use '{project_name}' as the project parameter in MCP tool calls.\n"

        return result


@mcp.tool()
async def delete_project(project_name: str, context: Context | None = None) -> str:
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
    async with get_client() as client:
        # Check if server is constrained to a specific project
        constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
        if constrained_project:
            return f"# Error\n\nProject deletion disabled - MCP server is constrained to project '{constrained_project}'.\nUse the CLI to delete projects: `basic-memory project remove \"{project_name}\"`"

        if context:  # pragma: no cover
            await context.info(f"Deleting project: {project_name}")

        # Get project info before deletion to validate it exists
        response = await call_get(client, "/projects/projects")
        project_list = ProjectList.model_validate(response.json())

        # Find the project by name (case-insensitive) or permalink - same logic as switch_project
        project_permalink = generate_permalink(project_name)
        target_project = None
        for p in project_list.projects:
            # Match by permalink (handles case-insensitive input)
            if p.permalink == project_permalink:
                target_project = p
                break
            # Also match by name comparison (case-insensitive)
            if p.name.lower() == project_name.lower():
                target_project = p
                break

        if not target_project:
            available_projects = [p.name for p in project_list.projects]
            raise ValueError(
                f"Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"
            )

        # Call API to delete project using URL encoding for special characters
        from urllib.parse import quote

        encoded_name = quote(target_project.name, safe="")
        response = await call_delete(client, f"/projects/{encoded_name}")
        status_response = ProjectStatusResponse.model_validate(response.json())

        result = f"✓ {status_response.message}\n\n"

        if status_response.old_project:
            result += "Removed project details:\n"
            result += f"• Name: {status_response.old_project.name}\n"
            if hasattr(status_response.old_project, "path"):
                result += f"• Path: {status_response.old_project.path}\n"

        result += "Files remain on disk but project is no longer tracked by Basic Memory.\n"
        result += "Re-add the project to access its content again.\n"

        return result
