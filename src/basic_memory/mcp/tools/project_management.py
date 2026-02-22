"""Project management tools for Basic Memory MCP server.

These tools allow users to switch between projects, list available projects,
and manage project context during conversations.
"""

import os
from typing import Literal

from fastmcp import Context
from loguru import logger

from basic_memory.config import ConfigManager, has_cloud_credentials
from basic_memory.mcp.async_client import get_client, get_cloud_proxy_client, is_factory_mode
from basic_memory.mcp.server import mcp
from basic_memory.schemas.project_info import ProjectInfoRequest, ProjectItem, ProjectList
from basic_memory.utils import generate_permalink


# --- Helpers for dual-fetch + merge ---


async def _fetch_cloud_projects(
    workspace: str | None = None,
    context: Context | None = None,
) -> ProjectList | None:
    """Fetch projects from the cloud API, returning None on failure.

    Logs warnings on failure so the caller can fall back to local-only results.
    """
    try:
        from basic_memory.mcp.clients import ProjectClient

        async with get_cloud_proxy_client(workspace=workspace) as cloud_client:
            cloud_project_client = ProjectClient(cloud_client)
            cloud_list = await cloud_project_client.list_projects()
        if context:  # pragma: no cover
            await context.info(f"Discovered {len(cloud_list.projects)} cloud projects")
        return cloud_list
    except Exception as exc:
        logger.warning(f"Cloud project discovery failed: {exc}")
        if context:  # pragma: no cover
            await context.info("Cloud project discovery failed, showing local projects only")
        return None


def _merge_projects(
    local_list: ProjectList | None,
    cloud_list: ProjectList | None,
    *,
    cloud_workspace_name: str | None = None,
    cloud_workspace_type: str | None = None,
    cloud_workspace_tenant_id: str | None = None,
) -> list[dict]:
    """Merge local and cloud project lists by permalink.

    Returns a sorted list of dicts with unified project metadata.
    Same merge-by-permalink algorithm used by the CLI `bm project list`.
    """
    names_by_permalink: dict[str, str] = {}
    local_by_permalink: dict[str, ProjectItem] = {}
    cloud_by_permalink: dict[str, ProjectItem] = {}

    if local_list:
        for project in local_list.projects:
            permalink = generate_permalink(project.name)
            names_by_permalink[permalink] = project.name
            local_by_permalink[permalink] = project

    if cloud_list:
        for project in cloud_list.projects:
            permalink = generate_permalink(project.name)
            names_by_permalink[permalink] = project.name
            cloud_by_permalink[permalink] = project

    merged: list[dict] = []
    for permalink in sorted(names_by_permalink):
        name = names_by_permalink[permalink]
        local_proj = local_by_permalink.get(permalink)
        cloud_proj = cloud_by_permalink.get(permalink)

        # Determine source label
        if local_proj and cloud_proj:
            source = "local+cloud"
        elif cloud_proj:
            source = "cloud"
        else:
            source = "local"

        # Prefer local path for backward compat; fall back to cloud path
        local_path = local_proj.path if local_proj else None
        cloud_path = cloud_proj.path if cloud_proj else None
        path = local_path or cloud_path or ""

        is_default = False
        if local_proj and local_proj.is_default:
            is_default = True
        if cloud_proj and cloud_proj.is_default:
            is_default = True

        # Prefer cloud display_name / is_private (cloud injects these)
        display_name = None
        is_private = False
        if cloud_proj:
            display_name = cloud_proj.display_name
            is_private = cloud_proj.is_private
        elif local_proj:
            display_name = local_proj.display_name
            is_private = local_proj.is_private

        # Attach workspace info for cloud-sourced projects
        ws_name = cloud_workspace_name if cloud_proj else None
        ws_type = cloud_workspace_type if cloud_proj else None
        ws_tenant_id = cloud_workspace_tenant_id if cloud_proj else None

        merged.append(
            {
                "name": name,
                "path": path,
                "local_path": local_path,
                "cloud_path": cloud_path,
                "source": source,
                "is_default": is_default,
                "is_private": is_private,
                "display_name": display_name,
                "workspace_name": ws_name,
                "workspace_type": ws_type,
                "workspace_tenant_id": ws_tenant_id,
            }
        )

    return merged


def _format_project_list_text(merged: list[dict]) -> str:
    """Format merged project list as human-readable text."""
    result = "Available projects:\n"
    for project in merged:
        display_name = project["display_name"]
        name = project["name"]
        label = f"{display_name} ({name})" if display_name else name
        source = project["source"]
        result += f"• {label} ({source})\n"

    result += "\n" + "─" * 40 + "\n"
    result += "Next: Ask which project to use for this session.\n"
    result += "Example: 'Which project should I use for this task?'\n\n"
    result += (
        "Session reminder: Track the selected project for all subsequent "
        "operations in this conversation.\n"
    )
    result += "The user can say 'switch to [project]' to change projects."
    return result


def _format_project_list_json(
    merged: list[dict],
    default_project: str | None,
    constrained_project: str | None,
) -> dict:
    """Format merged project list as structured JSON."""
    return {
        "projects": merged,
        "default_project": default_project,
        "constrained_project": constrained_project,
    }


@mcp.tool(
    "list_memory_projects",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def list_memory_projects(
    output_format: Literal["text", "json"] = "text",
    workspace: str | None = None,
    context: Context | None = None,
) -> str | dict:
    """List all available projects with their status.

    Shows projects from both local and cloud sources when cloud credentials
    are available, merging by permalink to give a unified view.

    Args:
        output_format: "text" returns the existing human-readable project list.
            "json" returns structured project metadata.
        workspace: Cloud workspace name or tenant_id. Falls back to
            config.default_workspace when not specified.
        context: Optional FastMCP context for progress/status logging.
    """
    if context:  # pragma: no cover
        await context.info("Listing all available projects")

    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")

    from basic_memory.mcp.clients import ProjectClient

    # --- Factory mode (cloud app) ---
    # Trigger: set_client_factory() was called (e.g., basic-memory-cloud)
    # Why: there is no local ASGI server; the factory IS the only source
    # Outcome: single fetch, no merge needed
    if is_factory_mode():
        async with get_client() as client:
            project_client = ProjectClient(client)
            project_list = await project_client.list_projects()

        merged = _merge_projects(project_list, None)
        if output_format == "json":
            return _format_project_list_json(
                merged, project_list.default_project, constrained_project
            )
        if constrained_project:
            return _format_constrained_text(constrained_project)
        return _format_project_list_text(merged)

    # --- Normal MCP stdio mode ---
    # Always fetch local projects via the ASGI transport
    async with get_client() as client:
        project_client = ProjectClient(client)
        local_list = await project_client.list_projects()

    # Fetch cloud projects when credentials are available
    cloud_list: ProjectList | None = None
    cloud_ws_name: str | None = None
    cloud_ws_type: str | None = None
    cloud_ws_tenant_id: str | None = None
    config = ConfigManager().config
    if has_cloud_credentials(config):
        # Use explicit workspace, fall back to config default
        effective_workspace = workspace or config.default_workspace
        cloud_list = await _fetch_cloud_projects(effective_workspace, context)

        # Resolve workspace metadata so each cloud project carries its workspace info
        if cloud_list:
            cloud_ws_tenant_id = effective_workspace
            try:
                from basic_memory.mcp.project_context import get_available_workspaces

                workspaces = await get_available_workspaces(context)
                matched = next(
                    (ws for ws in workspaces if ws.tenant_id == effective_workspace),
                    None,
                )
                if matched:
                    cloud_ws_name = matched.name
                    cloud_ws_type = matched.workspace_type
            except Exception:
                pass  # workspace lookup is best-effort

    merged = _merge_projects(
        local_list,
        cloud_list,
        cloud_workspace_name=cloud_ws_name,
        cloud_workspace_type=cloud_ws_type,
        cloud_workspace_tenant_id=cloud_ws_tenant_id,
    )
    default_project = local_list.default_project

    if output_format == "json":
        return _format_project_list_json(merged, default_project, constrained_project)

    if constrained_project:
        return _format_constrained_text(constrained_project)

    return _format_project_list_text(merged)


def _format_constrained_text(constrained_project: str) -> str:
    """Format text output when the MCP server is constrained to a single project."""
    result = f"Project: {constrained_project}\n\n"
    result += "Note: This MCP server is constrained to a single project.\n"
    result += "All operations will automatically use this project."
    return result


@mcp.tool(
    "create_memory_project",
    annotations={"destructiveHint": False, "openWorldHint": False},
)
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


@mcp.tool(
    annotations={"destructiveHint": True, "openWorldHint": False},
)
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
