"""Project management tools for Basic Memory MCP server.

These tools allow users to switch between projects, list available projects,
and manage project context during conversations.
"""

import os
from typing import Literal

from fastmcp import Context
from loguru import logger

from basic_memory.config import (
    BasicMemoryConfig,
    ConfigManager,
    ProjectEntry,
    has_cloud_credentials,
)
from basic_memory.mcp.async_client import (
    _explicit_routing,
    _force_local_mode,
    get_client,
    is_factory_mode,
)
from basic_memory.mcp.project_context import (
    WorkspaceProjectEntry,
    ensure_workspace_project_index,
    resolve_workspace_parameter,
)
from basic_memory.mcp.server import mcp
from basic_memory.schemas.project_info import ProjectInfoRequest, ProjectItem, ProjectList
from basic_memory.utils import generate_permalink


# --- Helpers for dual-fetch + merge ---


def _merge_projects(
    local_list: ProjectList | None,
    cloud_list: ProjectList | None,
    *,
    cloud_workspace_name: str | None = None,
    cloud_workspace_type: str | None = None,
    cloud_workspace_tenant_id: str | None = None,
    cloud_workspace_slug: str | None = None,
    cloud_workspace_is_default: bool = False,
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

        proj = cloud_proj or local_proj
        external_id = proj.external_id if proj else None

        merged.append(
            {
                "name": name,
                "external_id": external_id,
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
                "workspace_slug": cloud_workspace_slug if cloud_proj else None,
                "workspace_is_default": cloud_workspace_is_default if cloud_proj else False,
                "qualified_name": (
                    f"{cloud_workspace_slug}/{permalink}"
                    if cloud_proj and cloud_workspace_slug
                    else None
                ),
            }
        )

    return merged


def _workspace_entry_priority(entry: WorkspaceProjectEntry) -> tuple[bool, int, str, str]:
    """Prefer default/personal workspaces when duplicate project permalinks exist."""
    workspace_type_rank = 0 if entry.workspace.workspace_type == "personal" else 1
    return (
        # False sorts before True, so the cloud/default workspace comes first.
        not entry.workspace.is_default,
        workspace_type_rank,
        entry.workspace.name.casefold(),
        entry.workspace.tenant_id,
    )


def _select_attached_cloud_entry(
    cloud_entries: tuple[WorkspaceProjectEntry, ...],
    *,
    config_entry: ProjectEntry | None,
    config: BasicMemoryConfig | None,
) -> WorkspaceProjectEntry | None:
    """Choose the single cloud row that should inherit local project state."""
    if not cloud_entries:
        return None

    preferred_workspace_ids: list[str] = []
    if config_entry and config_entry.workspace_id:
        preferred_workspace_ids.append(config_entry.workspace_id)
    if (
        config
        and config.default_workspace
        and config.default_workspace not in preferred_workspace_ids
    ):
        preferred_workspace_ids.append(config.default_workspace)

    # The configured default workspace can differ from the cloud-side default.
    # Use the cloud default only after explicit local config preferences.
    default_workspace_entry = next(
        (entry for entry in cloud_entries if entry.workspace.is_default),
        None,
    )
    if (
        default_workspace_entry is not None
        and default_workspace_entry.workspace.tenant_id not in preferred_workspace_ids
    ):
        preferred_workspace_ids.append(default_workspace_entry.workspace.tenant_id)

    for workspace_id in preferred_workspace_ids:
        for entry in cloud_entries:
            if entry.workspace.tenant_id == workspace_id:
                return entry

    if len(cloud_entries) == 1:
        return cloud_entries[0]

    return sorted(cloud_entries, key=_workspace_entry_priority)[0]


def _merge_workspace_projects(
    local_list: ProjectList | None,
    cloud_entries: tuple[WorkspaceProjectEntry, ...],
    *,
    config: BasicMemoryConfig | None = None,
) -> list[dict]:
    """Merge local projects with cloud projects from every accessible workspace."""
    local_by_permalink: dict[str, ProjectItem] = {}
    if local_list:
        for project in local_list.projects:
            local_by_permalink[project.permalink] = project

    config_by_permalink: dict[str, ProjectEntry] = {}
    if config:
        config_by_permalink = {
            generate_permalink(project_name): entry
            for project_name, entry in config.projects.items()
        }

    cloud_entries_by_permalink: dict[str, list[WorkspaceProjectEntry]] = {}
    for entry in cloud_entries:
        cloud_entries_by_permalink.setdefault(entry.project.permalink, []).append(entry)

    attached_entry_by_permalink: dict[str, WorkspaceProjectEntry | None] = {}
    for permalink in local_by_permalink:
        attached_entry_by_permalink[permalink] = _select_attached_cloud_entry(
            tuple(cloud_entries_by_permalink.get(permalink, ())),
            config_entry=config_by_permalink.get(permalink),
            config=config,
        )

    cloud_permalinks = {entry.project.permalink for entry in cloud_entries}
    merged: list[dict] = []

    for entry in sorted(
        cloud_entries,
        key=lambda item: (*_workspace_entry_priority(item), item.project.permalink),
    ):
        permalink = entry.project.permalink
        local_proj = (
            local_by_permalink.get(permalink)
            # WorkspaceProjectEntry is a frozen dataclass containing Pydantic
            # models, so value equality is the intended comparison here.
            if attached_entry_by_permalink.get(permalink) == entry
            else None
        )
        cloud_proj = entry.project
        source = "local+cloud" if local_proj else "cloud"
        local_path = local_proj.path if local_proj else None
        cloud_path = cloud_proj.path

        merged.append(
            {
                "name": cloud_proj.name,
                "external_id": cloud_proj.external_id,
                "path": local_path or cloud_path,
                "local_path": local_path,
                "cloud_path": cloud_path,
                "source": source,
                "is_default": bool((local_proj and local_proj.is_default) or cloud_proj.is_default),
                "is_private": cloud_proj.is_private,
                "display_name": cloud_proj.display_name,
                "workspace_name": entry.workspace.name,
                "workspace_type": entry.workspace.workspace_type,
                "workspace_tenant_id": entry.workspace.tenant_id,
                "workspace_slug": entry.workspace.slug,
                "workspace_is_default": entry.workspace.is_default,
                "qualified_name": entry.qualified_name,
            }
        )

    if local_list:
        for project in sorted(local_list.projects, key=lambda item: item.permalink):
            if project.permalink in cloud_permalinks:
                continue
            merged.append(
                {
                    "name": project.name,
                    "external_id": project.external_id,
                    "path": project.path,
                    "local_path": project.path,
                    "cloud_path": None,
                    "source": "local",
                    "is_default": project.is_default,
                    "is_private": project.is_private,
                    "display_name": project.display_name,
                    "workspace_name": None,
                    "workspace_type": None,
                    "workspace_tenant_id": None,
                    "workspace_slug": None,
                    "workspace_is_default": False,
                    "qualified_name": None,
                }
            )

    return merged


def _format_project_list_text(merged: list[dict]) -> str:
    """Format merged project list as human-readable text."""
    result = "Available projects:\n"

    current_workspace: tuple[str | None, str | None] | None = None
    for project in merged:
        workspace_slug = project.get("workspace_slug")
        workspace_name = project.get("workspace_name")
        if workspace_slug:
            workspace_key = (workspace_slug, workspace_name)
            if workspace_key != current_workspace:
                default_label = " default" if project.get("workspace_is_default") else ""
                result += f"\nWorkspace: {workspace_name} ({workspace_slug}{default_label})\n"
                current_workspace = workspace_key
        elif current_workspace is not None:
            result += "\nLocal projects:\n"
            current_workspace = None

        display_name = project["display_name"]
        name = project["name"]
        label = f"{display_name} ({name})" if display_name else name
        source = project["source"]
        external_id = project.get("external_id", "")
        id_suffix = f" [{external_id}]" if external_id else ""
        result += f"- {label} ({source}){id_suffix}\n"

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
    context: Context | None = None,
) -> str | dict:
    """List all available projects with their status.

    Shows projects from both local and cloud sources when cloud credentials
    are available, merging by permalink to give a unified view.

    Each project entry includes an `external_id` (UUID). Pass that value as the
    `project_id` parameter on other tools to address a specific project
    unambiguously across cloud workspaces — useful when the same project name
    exists in more than one workspace.

    Args:
        output_format: "text" returns the existing human-readable project list.
            "json" returns structured project metadata.
        context: Optional FastMCP context for progress/status logging.
    """
    if context:  # pragma: no cover
        await context.info("Listing all available projects")

    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")

    from basic_memory.mcp.clients import ProjectClient

    # --- Factory mode (cloud app) ---
    # Trigger: set_client_factory() was called (e.g., basic-memory-cloud)
    # Why: there is no local ASGI server; the factory IS the cloud source
    # Outcome: fetch every accessible workspace so callers can discover cross-workspace IDs
    if is_factory_mode():
        workspace_index = await ensure_workspace_project_index(context=context)
        merged = _merge_workspace_projects(None, workspace_index.entries)
        default_project = next(
            (
                entry.project.name
                for entry in workspace_index.entries
                if entry.workspace.is_default and entry.project.is_default
            ),
            None,
        )
        if output_format == "json":
            return _format_project_list_json(merged, default_project, constrained_project)
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
    cloud_entries: tuple[WorkspaceProjectEntry, ...] = ()
    cloud_ws_name: str | None = None
    cloud_ws_type: str | None = None
    cloud_ws_tenant_id: str | None = None
    cloud_ws_slug: str | None = None
    cloud_ws_is_default = False
    config = ConfigManager().config
    if has_cloud_credentials(config):
        try:
            workspace_index = await ensure_workspace_project_index(context=context)
            cloud_entries = workspace_index.entries
        except Exception as exc:
            logger.warning(
                f"Cloud workspace project index discovery failed while listing projects; "
                f"showing local-only project list: {exc}"
            )
            if context:  # pragma: no cover
                await context.info(
                    "Cloud workspace project discovery failed while listing projects; "
                    "showing local projects only"
                )

    if cloud_entries:
        merged = _merge_workspace_projects(local_list, cloud_entries, config=config)
    else:
        merged = _merge_projects(
            local_list,
            cloud_list,
            cloud_workspace_name=cloud_ws_name,
            cloud_workspace_type=cloud_ws_type,
            cloud_workspace_tenant_id=cloud_ws_tenant_id,
            cloud_workspace_slug=cloud_ws_slug,
            cloud_workspace_is_default=cloud_ws_is_default,
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


async def _resolve_workspace_routing(
    workspace: str | None,
    context: Context | None,
) -> str | None:
    """Resolve an optional workspace selector to the routing tenant id."""
    if workspace is None:
        return None

    explicit_cloud_routing = _explicit_routing() and not _force_local_mode()
    config = ConfigManager().config
    should_resolve_workspace = is_factory_mode() or (
        explicit_cloud_routing and has_cloud_credentials(config)
    )
    if not should_resolve_workspace:
        return workspace

    # Trigger: cloud routing can use workspace discovery and the caller supplied
    #   a friendly selector such as a slug, name, or tenant id.
    # Why: MCP callers should not need to paste UUIDs, but the transport still
    #   uses X-Workspace-ID with the tenant id as its routing authority.
    # Outcome: resolve once before the project-management request and pass only
    #   the tenant id downstream.
    resolved_workspace = await resolve_workspace_parameter(workspace=workspace, context=context)
    return resolved_workspace.tenant_id


@mcp.tool(
    "create_memory_project",
    annotations={"destructiveHint": False, "openWorldHint": False},
)
async def create_memory_project(
    project_name: str,
    project_path: str,
    set_default: bool = False,
    workspace: str | None = None,
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
        workspace: Optional cloud workspace selector to create the project in. Slug is
            preferred for AI callers, but tenant_id and unique name are also accepted.
            When omitted, the connection's default workspace is used. Discover values
            via `list_workspaces`. In local mode the selector is passed through
            without slug resolution.
        output_format: "text" returns the existing human-readable result text.
            "json" returns structured project creation metadata.
        context: Optional FastMCP context for progress/status logging.

    Returns:
        Confirmation message with project details

    Example:
        create_memory_project("my-research", "~/Documents/research")
        create_memory_project("work-notes", "/home/user/work", set_default=True)
        create_memory_project("team-notes", "/team/notes", workspace="team-paul")
    """
    # Trigger: MCP server is constrained to a single project.
    # Why: constrained sessions cannot create projects, and workspace selectors
    # may be invalid or unavailable in that locked context.
    # Outcome: return the existing disabled response before opening a routed client.
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

    workspace_id = await _resolve_workspace_routing(workspace, context)

    # workspace targets a non-default cloud workspace at create time.
    # Trigger: caller passed workspace (e.g. a slug discovered via list_workspaces).
    # Why: there is no project_id yet for per-project routing — the project doesn't exist.
    # Outcome: cloud factory routes the create request to the resolved workspace tenant id.
    async with get_client(workspace=workspace_id) as client:
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
                    "external_id": existing_match.external_id,
                    "path": existing_match.path,
                    "is_default": is_default,
                    "created": False,
                    "already_exists": True,
                }
            return (
                f"✓ Project already exists: {existing_match.name}\n\n"
                f"Project Details:\n"
                f"• Name: {existing_match.name}\n"
                f"• External ID: {existing_match.external_id}\n"
                f"• Path: {existing_match.path}\n"
                f"{'• Set as default project\n' if is_default else ''}"
                "\nProject is already available for use in tool calls.\n"
            )

        status_response = await project_client.create_project(project_request.model_dump())
        from basic_memory.mcp.project_context import invalidate_workspace_project_index

        await invalidate_workspace_project_index(context)

        if output_format == "json":
            new_project = status_response.new_project
            return {
                "name": new_project.name if new_project else project_name,
                "external_id": new_project.external_id if new_project else None,
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
            result += f"• External ID: {status_response.new_project.external_id}\n"
            result += f"• Path: {status_response.new_project.path}\n"

            if set_default:
                result += "• Set as default project\n"

        result += "\nProject is now available for use in tool calls.\n"
        result += f"Use '{project_name}' as the project parameter in MCP tool calls.\n"

        return result


@mcp.tool(
    annotations={"destructiveHint": True, "openWorldHint": False},
)
async def delete_project(
    project_name: str,
    workspace: str | None = None,
    context: Context | None = None,
) -> str:
    """Delete a Basic Memory project.

    Removes a project from the configuration and database. This does NOT delete
    the actual files on disk - only removes the project from Basic Memory's
    configuration and database records.

    Args:
        project_name: Name of the project to delete
        workspace: Optional cloud workspace selector to delete the project from.
            Slug is preferred for AI callers, but tenant_id and unique name are
            also accepted. When omitted, the connection's default workspace is
            used. In local mode the selector is passed through without slug
            resolution, matching create_memory_project behavior.

    Returns:
        Confirmation message about project deletion

    Example:
        delete_project("old-project")
        delete_project("team-project", workspace="team-paul")

    Warning:
        This action cannot be undone. The project will need to be re-added
        to access its content through Basic Memory again.
    """
    # Trigger: MCP server is constrained to a single project.
    # Why: constrained sessions cannot delete projects, and workspace selectors
    # may be invalid or unavailable in that locked context.
    # Outcome: return the existing disabled message before opening a routed client.
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project:
        return f"# Error\n\nProject deletion disabled - MCP server is constrained to project '{constrained_project}'.\nUse the CLI to delete projects: `basic-memory project remove \"{project_name}\"`"

    workspace_id = await _resolve_workspace_routing(workspace, context)
    async with get_client(workspace=workspace_id) as client:
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
        from basic_memory.mcp.project_context import invalidate_workspace_project_index

        await invalidate_workspace_project_index(context)

        result = f"✓ {status_response.message}\n\n"

        if status_response.old_project:
            result += "Removed project details:\n"
            result += f"• Name: {status_response.old_project.name}\n"
            if hasattr(status_response.old_project, "path"):
                result += f"• Path: {status_response.old_project.path}\n"

        result += "Files remain on disk but project is no longer tracked by Basic Memory.\n"
        result += "Re-add the project to access its content again.\n"

        return result
