"""Project management tools for Basic Memory MCP server.

These tools allow users to switch between projects, list available projects,
and manage project context during conversations.
"""

import os
from typing import Literal
from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.server import mcp
from basic_memory.schemas.project_info import ProjectInfoRequest
from basic_memory.utils import generate_permalink


@mcp.tool("list_memory_projects")
async def list_memory_projects(
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict:
    """List all available projects with their status.

    Args:
        output_format: "text" returns the existing human-readable project list.
            "json" returns structured project metadata.
        context: Optional FastMCP context for progress/status logging.
    """
    async with get_client() as client:
        if context:  # pragma: no cover
            await context.info("Listing all available projects")

        constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")

        from basic_memory.mcp.clients import ProjectClient

        project_client = ProjectClient(client)
        project_list = await project_client.list_projects()

        if output_format == "json":
            projects = [
                {
                    "name": project.name,
                    "path": project.path,
                    "is_default": project.is_default,
                    # Reserved for forward-compatible cloud/private project metadata.
                    # Local project list responses do not currently provide these values.
                    "is_private": False,
                    "display_name": None,
                }
                for project in project_list.projects
            ]
            return {
                "projects": projects,
                "default_project": project_list.default_project,
                "constrained_project": constrained_project,
            }

        if constrained_project:
            result = f"Project: {constrained_project}\n\n"
            result += "Note: This MCP server is constrained to a single project.\n"
            result += "All operations will automatically use this project."
            return result

        result = "Available projects:\n"
        for project in project_list.projects:
            label = (
                f"{project.display_name} ({project.name})"
                if project.display_name
                else project.name
            )
            result += f"• {label}\n"

        result += "\n" + "─" * 40 + "\n"
        result += "Next: Ask which project to use for this session.\n"
        result += "Example: 'Which project should I use for this task?'\n\n"
        result += "Session reminder: Track the selected project for all subsequent operations in this conversation.\n"
        result += "The user can say 'switch to [project]' to change projects."
        return result


@mcp.tool("create_memory_project")
async def create_memory_project(
    project_name: str,
    project_path: str,
    set_default: bool = False,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict:
    """Create a new Basic Memory project.

    Creates a new project with the specified name and path. The project directory
    will be created if it doesn't exist. Optionally sets the new project as default.

    Args:
        project_name: Name for the new project (must be unique)
        project_path: File system path where the project will be stored
        set_default: Whether to set this project as the default (optional, defaults to False)
        output_format: "text" returns the existing human-readable result text.
            "json" returns structured project creation metadata.
        context: Optional FastMCP context for progress/status logging.

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
            if output_format == "json":
                return {
                    "name": project_name,
                    "path": project_path,
                    "is_default": False,
                    "created": False,
                    "already_exists": False,
                    "error": "PROJECT_CONSTRAINED",
                    "message": (
                        f"Project creation disabled - MCP server is constrained to project "
                        f"'{constrained_project}'."
                    ),
                }
            return f'# Error\n\nProject creation disabled - MCP server is constrained to project \'{constrained_project}\'.\nUse the CLI to create projects: `basic-memory project add "{project_name}" "{project_path}"`'

        if context:  # pragma: no cover
            await context.info(f"Creating project: {project_name} at {project_path}")

        # Create the project request
        project_request = ProjectInfoRequest(
            name=project_name, path=project_path, set_default=set_default
        )

        # Import here to avoid circular import
        from basic_memory.mcp.clients import ProjectClient

        # Use typed ProjectClient for API calls
        project_client = ProjectClient(client)
        existing = await project_client.list_projects()
        existing_match = next(
            (p for p in existing.projects if p.name.casefold() == project_name.casefold()),
            None,
        )
        if existing_match:
            is_default = bool(
                existing_match.is_default or existing.default_project == existing_match.name
            )
            if output_format == "json":
                return {
                    "name": existing_match.name,
                    "path": existing_match.path,
                    "is_default": is_default,
                    "created": False,
                    "already_exists": True,
                }
            return (
                f"✓ Project already exists: {existing_match.name}\n\n"
                f"Project Details:\n"
                f"• Name: {existing_match.name}\n"
                f"• Path: {existing_match.path}\n"
                f"{'• Set as default project\n' if is_default else ''}"
                "\nProject is already available for use in tool calls.\n"
            )

        status_response = await project_client.create_project(project_request.model_dump())

        if output_format == "json":
            new_project = status_response.new_project
            return {
                "name": new_project.name if new_project else project_name,
                "path": new_project.path if new_project else project_path,
                "is_default": bool(
                    (new_project.is_default if new_project else False) or set_default
                ),
                "created": True,
                "already_exists": False,
            }

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

        # Import here to avoid circular import
        from basic_memory.mcp.clients import ProjectClient

        # Use typed ProjectClient for API calls
        project_client = ProjectClient(client)

        # Get project info before deletion to validate it exists
        project_list = await project_client.list_projects()

        # Find the project by permalink (derived from name).
        # Note: The API response uses `ProjectItem` which derives `permalink` from `name`,
        # so a separate case-insensitive name match would be redundant here.
        project_permalink = generate_permalink(project_name)
        target_project = None
        for p in project_list.projects:
            # Match by permalink (handles case-insensitive input)
            if p.permalink == project_permalink:
                target_project = p
                break

        if not target_project:
            available_projects = [p.name for p in project_list.projects]
            raise ValueError(
                f"Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"
            )

        # Delete project using project external_id
        status_response = await project_client.delete_project(target_project.external_id)

        result = f"✓ {status_response.message}\n\n"

        if status_response.old_project:
            result += "Removed project details:\n"
            result += f"• Name: {status_response.old_project.name}\n"
            if hasattr(status_response.old_project, "path"):
                result += f"• Path: {status_response.old_project.path}\n"

        result += "Files remain on disk but project is no longer tracked by Basic Memory.\n"
        result += "Re-add the project to access its content again.\n"

        return result
