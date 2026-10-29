"""Project listing, creation and deletion behind bm_project and bm_project_delete.

The project listing also serves bm_search across projects and the Cloud server.
"""

import os
from typing import Any, Literal

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
    _force_cloud_mode,
    _force_local_mode,
    get_client,
    is_factory_mode,
)
from basic_memory.mcp.project_context import (
    WorkspaceProjectEntry,
    ensure_workspace_project_index,
    resolve_workspace_parameter,
)
from basic_memory.mcp.refusals import api_refusals, refusal
from basic_memory.schemas.project_info import ProjectInfoRequest, ProjectItem, ProjectList
from basic_memory.utils import generate_permalink


# --- Helpers for dual-fetch + merge ---


# Push/pull works on every workspace type (rclone on Personal, WebDAV on Team), so
# every project supports local sync. The keys stay for clients that read them and
# match `bm project list --json`; only the deprecated mirror commands were
# Personal-only (#1596).
SYNC_SUPPORT_METADATA: dict[str, object] = {
    "sync_supported": True,
    "sync_reason": None,
    "local_usage": "sync-supported",
}


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

    return sorted(cloud_entries, key=_workspace_entry_priority)[0]


def _merge_workspace_projects(
    local_list: ProjectList | None,
    cloud_entries: tuple[WorkspaceProjectEntry, ...],
    *,
    config: BasicMemoryConfig | None = None,
) -> list[dict[str, Any]]:
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
    merged: list[dict[str, Any]] = []

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
                "id": cloud_proj.id,
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
                **SYNC_SUPPORT_METADATA,
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
                    "id": project.id,
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
                    **SYNC_SUPPORT_METADATA,
                    "qualified_name": None,
                }
            )

    return merged


def _format_project_list_text(merged: list[dict[str, Any]]) -> str:
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
    result += (
        "Pass a project name (or its [external_id] as project_id) on tool calls to "
        "target a project. Calls without one use the default project."
    )
    return result


def _format_project_list_json(
    merged: list[dict[str, Any]],
    default_project: str | None,
    constrained_project: str | None,
) -> dict[str, Any]:
    """Format merged project list as structured JSON."""
    # Trigger: BASIC_MEMORY_MCP_PROJECT pins this server to one project
    # Why: text output already shows only that project; JSON must not list the others
    # Outcome: projects holds only entries the constraint names, by bare name,
    # workspace-qualified name, or external_id — every spelling routing accepts
    if constrained_project:
        constrained_permalink = generate_permalink(constrained_project)
        merged = [
            project
            for project in merged
            if constrained_project == project["external_id"]
            or constrained_permalink
            in {
                generate_permalink(spelling)
                for spelling in (project["name"], project["qualified_name"])
                if spelling
            }
        ]
    return {
        "projects": merged,
        "default_project": default_project,
        "constrained_project": constrained_project,
    }


async def list_memory_projects(
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict[str, Any]:
    """List all available projects with their status.

    Shows projects from both local and cloud sources when cloud credentials
    are available, merging by permalink to give a unified view.

    Each project entry includes an `external_id` (UUID). Pass that value as the
    `project_id` parameter on other tools to address a specific project
    unambiguously across cloud workspaces — useful when the same project name
    exists in more than one workspace.

    Args:
        output_format: "text" returns a human-readable project list.
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

    # Fetch cloud projects from every accessible workspace when credentials are available
    cloud_entries: tuple[WorkspaceProjectEntry, ...] = ()
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

    merged = _merge_workspace_projects(local_list, cloud_entries, config=config)
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
    output_format: Literal["text", "json"],
) -> str | None:
    """Resolve an optional workspace selector to the routing tenant id.

    An unknown or ambiguous selector is an INVALID_ARGUMENTS refusal in the
    caller's output format, naming the workspaces it could have meant.
    """
    if workspace is None:
        return None

    forced_local = _explicit_routing() and _force_local_mode()
    config = ConfigManager().config
    # Resolve whenever credentials make workspace discovery possible — not only
    # under explicit --cloud. A workspace selector implies cloud routing
    # (get_client routes it to the cloud proxy, #954), and the transport needs
    # the tenant id in X-Workspace-ID, not a slug or display name.
    should_resolve_workspace = is_factory_mode() or (
        has_cloud_credentials(config) and not forced_local
    )
    if not should_resolve_workspace:
        return workspace

    # Trigger: cloud routing can use workspace discovery and the caller supplied
    #   a friendly selector such as a slug, name, or tenant id.
    # Why: MCP callers should not need to paste UUIDs, but the transport still
    #   uses X-Workspace-ID with the tenant id as its routing authority.
    # Outcome: resolve once before the project-management request and pass only
    #   the tenant id downstream.
    try:
        resolved_workspace = await resolve_workspace_parameter(workspace=workspace, context=context)
    except ValueError as error:
        raise refusal("INVALID_ARGUMENTS", str(error), output_format=output_format) from error
    return resolved_workspace.tenant_id


def _normalize_indexing_response(
    index_response: dict[str, object],
    *,
    run_in_background: bool,
) -> dict[str, object]:
    """Add a stable lifecycle state to backend-specific indexing details."""
    return {
        **index_response,
        "state": "accepted" if run_in_background else "completed",
    }


def _format_indexing_details(indexing: dict[str, object]) -> str:
    """Format indexing state and the details returned by either backend."""
    result = "Indexing:\n"
    result += f"• State: {indexing['state']}\n"
    if status := indexing.get("status"):
        result += f"• Status: {status}\n"
    if message := indexing.get("message"):
        result += f"• Message: {message}\n"
    if job_id := indexing.get("job_id"):
        result += f"• Job ID: {job_id}\n"
    for key, label in (
        ("total_files", "Files discovered"),
        ("enqueued_files", "Files enqueued"),
        ("enqueued_batches", "Batches enqueued"),
        ("deleted_files", "Orphans deleted"),
    ):
        if key in indexing:
            result += f"• {label}: {indexing[key]}\n"
    return result


async def create_memory_project(
    project_name: str,
    project_path: str,
    set_default: bool = False,
    workspace: str | None = None,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Create a new Basic Memory project.

    Creates a new project with the specified name and path. The project directory
    will be created if it doesn't exist. Optionally sets the new project as default.
    Repeating the same name and path is idempotent. The same name with a different
    path is an error.

    Args:
        project_name: Name for the new project (must be unique)
        project_path: File system path where the project will be stored
        set_default: Whether to set this project as the default (optional, defaults to False)
        workspace: Optional cloud workspace selector to create the project in. Slug is
            preferred for AI callers, but tenant_id and unique name are also accepted.
            When omitted, the connection's default workspace is used. Discover values
            via `bm_project(command='list')`. A workspace selector implies cloud routing:
            without cloud credentials the call fails fast instead of silently
            creating a local project.
        output_format: "text" returns human-readable result text.
            "json" returns structured project creation metadata.
        context: Optional FastMCP context for progress/status logging.

    Returns:
        Confirmation message with project details

    Example:
        bm_project(command="create", name="my-research", path="~/Documents/research")
        bm_project(command="create", name="work-notes", path="/home/user/work", set_default=True)
        bm_project(command="create", name="team-notes", path="/team/notes", workspace="team-paul")
    """
    # Trigger: MCP server is constrained to a single project.
    # Why: constrained sessions cannot create projects, and workspace selectors
    # may be invalid or unavailable in that locked context.
    # Outcome: an UNSUPPORTED refusal before opening a routed client.
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project:
        raise refusal(
            "UNSUPPORTED",
            f"Project creation disabled - MCP server is constrained to project "
            f"'{constrained_project}'.",
            try_instead=f'the CLI: basic-memory project add "{project_name}" "{project_path}"',
            output_format=output_format,
        )

    workspace_id = await _resolve_workspace_routing(workspace, context, output_format)

    # workspace targets a non-default cloud workspace at create time.
    # Trigger: caller passed workspace (e.g. a slug from bm_project(command="list")).
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
        index_in_background = _routes_to_cloud(workspace_id)
        if existing_match:
            # Trigger: a local project of this name is stored at another directory.
            # Why: an already-exists success would ignore the requested path and
            #   re-index the existing project (#1811). The API's add_project
            #   compares the same normalized paths.
            # Outcome: refuse before indexing. Cloud is not checked: hosted paths are
            #   tenant-relative and derived from the project name, so a requested
            #   path never moves a cloud project, and normalizing one here would
            #   resolve it against this process's working directory.
            requested_path = os.path.abspath(os.path.expanduser(project_path))
            existing_path = os.path.abspath(os.path.expanduser(existing_match.path))
            if not _routes_to_cloud(workspace_id) and requested_path != existing_path:
                raise refusal(
                    "ALREADY_EXISTS",
                    f"Project '{existing_match.name}' already exists at "
                    f"{existing_match.path}, not {project_path}.",
                    try_instead=(
                        f"another name, or bm_project(command='create', "
                        f"name='{existing_match.name}', path='{existing_match.path}')"
                    ),
                    output_format=output_format,
                )

            is_default = bool(
                existing_match.is_default or existing.default_project == existing_match.name
            )
            # Trigger: a previous create may have committed the project record
            # before its indexing request failed or timed out.
            # Why: create retries must repair that partial success instead of
            # permanently short-circuiting on the existing project record.
            # Outcome: idempotently request indexing again on the current backend.
            index_response = await project_client.index(
                existing_match.external_id,
                run_in_background=index_in_background,
            )
            indexing = _normalize_indexing_response(
                index_response,
                run_in_background=index_in_background,
            )
            if output_format == "json":
                return {
                    "name": existing_match.name,
                    "external_id": existing_match.external_id,
                    "path": existing_match.path,
                    "is_default": is_default,
                    "created": False,
                    "already_exists": True,
                    "indexing": indexing,
                }
            result = (
                f"✓ Project already exists: {existing_match.name}\n\n"
                f"Project Details:\n"
                f"• Name: {existing_match.name}\n"
                f"• External ID: {existing_match.external_id}\n"
                f"• Path: {existing_match.path}\n"
                f"{'• Set as default project\n' if is_default else ''}"
                "\n"
            )
            result += _format_indexing_details(indexing)
            if index_in_background:
                result += (
                    "\nProject already existed. Retained content will become readable and "
                    "searchable when indexing completes.\n"
                )
            else:
                result += (
                    "\nProject is already available for use in tool calls; indexing completed.\n"
                )
            return result

        # The API refuses some creates (a path inside another project, a name whose
        # permalink collides).
        with api_refusals(output_format):
            status_response = await project_client.create_project(project_request.model_dump())
        from basic_memory.mcp.project_context import invalidate_project_caches

        await invalidate_project_caches(context)

        new_project = status_response.new_project
        if new_project is None:
            raise RuntimeError("Project creation succeeded without returning the new project")

        # Local indexing can finish inline, so retained files are immediately
        # available. Cloud must use its durable background workflow; that route
        # awaits the PGQueuer handoff before acknowledging the request (#1084).
        index_response = await project_client.index(
            new_project.external_id,
            run_in_background=index_in_background,
        )
        indexing = _normalize_indexing_response(
            index_response,
            run_in_background=index_in_background,
        )

        if output_format == "json":
            return {
                "name": new_project.name,
                "external_id": new_project.external_id,
                "path": new_project.path,
                "is_default": bool(new_project.is_default or set_default),
                "created": True,
                "already_exists": False,
                "indexing": indexing,
            }

        result = f"✓ {status_response.message}\n\n"

        result += "Project Details:\n"
        result += f"• Name: {new_project.name}\n"
        result += f"• External ID: {new_project.external_id}\n"
        result += f"• Path: {new_project.path}\n"

        if set_default:
            result += "• Set as default project\n"

        result += "\n"
        result += _format_indexing_details(indexing)

        if index_in_background:
            result += (
                "\nProject created. Retained content will become readable and searchable "
                "when indexing completes.\n"
            )
        else:
            result += "\nProject is now available for use in tool calls; indexing completed.\n"
        result += f"Use '{project_name}' as the project parameter in MCP tool calls.\n"

        return result


def _routes_to_cloud(workspace_id: str | None) -> bool:
    """Mirror get_client's non-project routing for project lifecycle operations.

    Trigger: project creation and deletion need backend-specific behavior.
    Why: cloud indexing must be asynchronous, and cloud deletes always delete
        the project's files while local deletes keep them by default.
    Outcome: True when get_client(workspace=...) serves the request from a
        cloud backend (factory mode, explicit --cloud, or a workspace selector).
    """
    if is_factory_mode():
        return True
    if _explicit_routing():
        return _force_cloud_mode()
    return workspace_id is not None


def _format_note_file_delete_result(
    status: Literal["pending", "skipped", "complete", "failed"] | None,
    *,
    files_location: str,
) -> str:
    """Describe note-file deletion without overstating backend completion."""
    if status == "pending":
        return f"Note-file deletion {files_location} was queued and is pending.\n"
    if status == "complete":
        return f"Note files {files_location} were deleted along with the project.\n"
    if status == "failed":
        return f"Note-file deletion {files_location} failed; note files may remain.\n"
    if status == "skipped":
        return f"Note-file deletion {files_location} was skipped; note files remain.\n"
    return (
        f"Note-file deletion {files_location} did not report a completion status; "
        "note files may remain.\n"
    )


async def delete_project(
    project_name: str,
    delete_notes: bool = False,
    workspace: str | None = None,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Delete a Basic Memory project.

    Removes a project from Basic Memory's configuration and database records.
    Local projects keep their note files on disk unless delete_notes=True.
    Cloud projects always have their note files deleted from cloud storage;
    those files can be recovered only from a cloud snapshot.

    Args:
        project_name: Name of the project to delete
        delete_notes: Local projects only: also delete the note files from
            disk. Defaults to False, which only stops tracking the project.
            Ignored for cloud projects, whose files are always deleted.
        workspace: Optional cloud workspace selector to delete the project from.
            Slug is preferred for AI callers, but tenant_id and unique name are
            also accepted. When omitted, the connection's default workspace is
            used. A workspace selector implies cloud routing: without cloud
            credentials the call fails fast, as bm_project(command="create")
            does.

    Returns:
        Confirmation message describing what was deleted and whether note
        files were removed or retained.

    Example:
        bm_project_delete(name="old-project")
        bm_project_delete(name="old-project", delete_notes=True)
        bm_project_delete(name="team-project", workspace="team-paul")

    Warning:
        This action cannot be undone. For a local project with
        delete_notes=False the project must be re-added to access its content
        again; with delete_notes=True its note files are permanently deleted.
        For a cloud project the note files are always deleted and can be
        recovered only from a cloud snapshot.
    """
    # Trigger: MCP server is constrained to a single project.
    # Why: constrained sessions cannot delete projects, and workspace selectors
    # may be invalid or unavailable in that locked context.
    # Outcome: an UNSUPPORTED refusal before opening a routed client.
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project:
        raise refusal(
            "UNSUPPORTED",
            f"Project deletion disabled - MCP server is constrained to project "
            f"'{constrained_project}'.",
            try_instead=f'the CLI: basic-memory project remove "{project_name}"',
            output_format=output_format,
        )

    workspace_id = await _resolve_workspace_routing(workspace, context, output_format)
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
            raise refusal(
                "NOT_FOUND",
                f"Project '{project_name}' not found. Available projects: "
                f"{', '.join(available_projects)}",
                output_format=output_format,
            )

        # Trigger: the delete is served by a cloud backend.
        # Why: the cloud service always deletes a project's files on delete
        #   (basic-memory-cloud#2117); files kept under a deleted project were
        #   unreachable. Sending delete_notes=True makes the request say what
        #   the server does.
        # Outcome: local deletes honor delete_notes; cloud deletes always
        #   request file deletion.
        cloud_routed = _routes_to_cloud(workspace_id)
        files_deleted = delete_notes or cloud_routed

        # The API refuses some deletes (the default project, for one).
        with api_refusals(output_format):
            status_response = await project_client.delete_project(
                target_project.external_id, delete_notes=files_deleted
            )
        from basic_memory.mcp.project_context import invalidate_project_caches

        await invalidate_project_caches(context)

        # The backend's own account of what happened: a Cloud delete can be queued,
        # and its note files are always deleted whatever delete_notes said.
        if output_format == "json":
            return {
                "action": "deleted",
                "name": target_project.name,
                "external_id": target_project.external_id,
                "delete_notes": files_deleted,
                "deletion_status": status_response.deletion_status,
                "file_delete_status": status_response.file_delete_status if files_deleted else None,
                "job_id": status_response.job_id,
                "message": status_response.message,
            }

        result = f"✓ {status_response.message}\n\n"

        if status_response.old_project:
            result += "Removed project details:\n"
            result += f"• Name: {status_response.old_project.name}\n"
            result += f"• Path: {status_response.old_project.path}\n"

        if status_response.deletion_status or status_response.job_id:
            result += "\nDeletion tracking:\n"
            if status_response.deletion_status:
                result += f"• Project deletion status: {status_response.deletion_status}\n"
            if status_response.job_id:
                result += f"• Deletion job ID: {status_response.job_id}\n"

        files_location = "in cloud storage" if cloud_routed else "on disk"
        if files_deleted:
            result += _format_note_file_delete_result(
                status_response.file_delete_status,
                files_location=files_location,
            )
            if cloud_routed:
                result += "Deleted cloud files can be recovered only from a cloud snapshot.\n"
        else:
            result += (
                f"Note files remain {files_location} but the project is no longer "
                "tracked by Basic Memory.\n"
            )
            result += "Re-add the project to access its content again.\n"

        return result
