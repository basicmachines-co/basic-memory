"""Project context utilities for Basic Memory MCP server.

Provides project lookup utilities for MCP tools.
Handles project validation and context management in one place.

Note: This module uses ProjectResolver for unified project resolution.
The resolve_project_parameter function is a thin wrapper for backwards
compatibility with existing MCP tools.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, List, Tuple

from httpx import AsyncClient
from httpx._types import (
    HeaderTypes,
)
from loguru import logger
from fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.config import BasicMemoryConfig, ConfigManager, ProjectMode
from basic_memory.project_resolver import ProjectResolver
from basic_memory.schemas.cloud import WorkspaceInfo, WorkspaceListResponse
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.schemas.v2 import ProjectResolveResponse
from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import generate_permalink, normalize_project_reference


async def resolve_project_parameter(
    project: Optional[str] = None,
    allow_discovery: bool = False,
    default_project: Optional[str] = None,
) -> Optional[str]:
    """Resolve project parameter using unified linear priority chain.

    This is a thin wrapper around ProjectResolver for backwards compatibility.
    New code should consider using ProjectResolver directly for more detailed
    resolution information.

    Resolution order:
    1. ENV_CONSTRAINT: BASIC_MEMORY_MCP_PROJECT env var (highest priority)
    2. EXPLICIT: project parameter passed directly
    3. DEFAULT: default_project from config (if set)
    4. Fallback: discovery (if allowed) → NONE

    Args:
        project: Optional explicit project parameter
        allow_discovery: If True, allows returning None for discovery mode
            (used by tools like recent_activity that can operate across all projects)
        default_project: Optional explicit default project. If not provided, reads from ConfigManager.

    Returns:
        Resolved project name or None if no resolution possible
    """
    # Load config for any values not explicitly provided
    if default_project is None:
        config = ConfigManager().config
        default_project = config.default_project

    # Create resolver with configuration and resolve
    resolver = ProjectResolver.from_env(
        default_project=default_project,
    )
    result = resolver.resolve(project=project, allow_discovery=allow_discovery)
    return result.project


async def get_project_names(client: AsyncClient, headers: HeaderTypes | None = None) -> List[str]:
    # Deferred import to avoid circular dependency with tools
    from basic_memory.mcp.tools.utils import call_get

    response = await call_get(client, "/v2/projects/", headers=headers)
    project_list = ProjectList.model_validate(response.json())
    return [project.name for project in project_list.projects]


def _workspace_matches_identifier(workspace: WorkspaceInfo, identifier: str) -> bool:
    """Return True when identifier matches workspace tenant_id or name."""
    if workspace.tenant_id == identifier:
        return True
    return workspace.name.lower() == identifier.lower()


def _workspace_choices(workspaces: list[WorkspaceInfo]) -> str:
    """Format deterministic workspace choices for prompt-style errors."""
    return "\n".join(
        [
            (
                f"- {item.name} "
                f"(type={item.workspace_type}, role={item.role}, tenant_id={item.tenant_id})"
            )
            for item in workspaces
        ]
    )


async def get_available_workspaces(context: Optional[Context] = None) -> list[WorkspaceInfo]:
    """Load available cloud workspaces for the current authenticated user."""
    if context:
        cached_raw = await context.get_state("available_workspaces")
        if isinstance(cached_raw, list):
            return [WorkspaceInfo.model_validate(item) for item in cached_raw]

    from basic_memory.mcp.async_client import get_cloud_control_plane_client
    from basic_memory.mcp.tools.utils import call_get

    async with get_cloud_control_plane_client() as client:
        response = await call_get(client, "/workspaces/")
        workspace_list = WorkspaceListResponse.model_validate(response.json())

    if context:
        await context.set_state(
            "available_workspaces",
            [ws.model_dump() for ws in workspace_list.workspaces],
        )

    return workspace_list.workspaces


async def resolve_workspace_parameter(
    workspace: Optional[str] = None,
    context: Optional[Context] = None,
) -> WorkspaceInfo:
    """Resolve workspace using explicit input, session cache, and cloud discovery."""
    if context:
        cached_raw = await context.get_state("active_workspace")
        if isinstance(cached_raw, dict):
            cached_workspace = WorkspaceInfo.model_validate(cached_raw)
            if workspace is None or _workspace_matches_identifier(cached_workspace, workspace):
                logger.debug(f"Using cached workspace from context: {cached_workspace.tenant_id}")
                return cached_workspace

    workspaces = await get_available_workspaces(context=context)
    if not workspaces:
        raise ValueError(
            "No accessible workspaces found for this account. "
            "Ensure you have an active subscription and tenant access."
        )

    selected_workspace: WorkspaceInfo | None = None

    if workspace:
        matches = [item for item in workspaces if _workspace_matches_identifier(item, workspace)]
        if not matches:
            raise ValueError(
                f"Workspace '{workspace}' was not found.\n"
                f"Available workspaces:\n{_workspace_choices(workspaces)}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Workspace name '{workspace}' matches multiple workspaces. "
                "Use tenant_id instead.\n"
                f"Available workspaces:\n{_workspace_choices(workspaces)}"
            )
        selected_workspace = matches[0]
    elif len(workspaces) == 1:
        selected_workspace = workspaces[0]
    else:
        raise ValueError(
            "Multiple workspaces are available. Ask the user which workspace to use, then retry "
            "with the 'workspace' argument set to the tenant_id or unique name.\n"
            f"Available workspaces:\n{_workspace_choices(workspaces)}"
        )

    if context:
        await context.set_state("active_workspace", selected_workspace.model_dump())
        logger.debug(f"Cached workspace in context: {selected_workspace.tenant_id}")

    return selected_workspace


async def get_active_project(
    client: AsyncClient,
    project: Optional[str] = None,
    context: Optional[Context] = None,
    headers: HeaderTypes | None = None,
) -> ProjectItem:
    """Get and validate project, setting it in context if available.

    Args:
        client: HTTP client for API calls
        project: Optional project name (resolved using hierarchy)
        context: Optional FastMCP context to cache the result

    Returns:
        The validated project item

    Raises:
        ValueError: If no project can be resolved
        HTTPError: If project doesn't exist or is inaccessible
    """
    # Deferred import to avoid circular dependency with tools
    from basic_memory.mcp.tools.utils import call_post

    resolved_project = await resolve_project_parameter(project)
    if not resolved_project:
        project_names = await get_project_names(client, headers)
        raise ValueError(
            "No project specified. "
            "Either set 'default_project' in config, or use 'project' argument.\n"
            f"Available projects: {project_names}"
        )

    project = resolved_project

    # Check if already cached in context
    if context:
        cached_raw = await context.get_state("active_project")
        if isinstance(cached_raw, dict):
            cached_project = ProjectItem.model_validate(cached_raw)
            if cached_project.name == project:
                logger.debug(f"Using cached project from context: {project}")
                return cached_project

    # Validate project exists by calling API
    logger.debug(f"Validating project: {project}")
    response = await call_post(
        client,
        "/v2/projects/resolve",
        json={"identifier": project},
        headers=headers,
    )
    resolved = ProjectResolveResponse.model_validate(response.json())
    active_project = ProjectItem(
        id=resolved.project_id,
        external_id=resolved.external_id,
        name=resolved.name,
        path=resolved.path,
        is_default=resolved.is_default,
    )

    # Cache in context if available
    if context:
        await context.set_state("active_project", active_project.model_dump())
        logger.debug(f"Cached project in context: {project}")

    logger.debug(f"Validated project: {active_project.name}")
    return active_project


def _split_project_prefix(path: str) -> tuple[Optional[str], str]:
    """Split a possible project prefix from a memory URL path."""
    if "/" not in path:
        return None, path

    project_prefix, remainder = path.split("/", 1)
    if not project_prefix or not remainder:
        return None, path

    if "*" in project_prefix:
        return None, path

    return project_prefix, remainder


async def resolve_project_and_path(
    client: AsyncClient,
    identifier: str,
    project: Optional[str] = None,
    context: Optional[Context] = None,
    headers: HeaderTypes | None = None,
) -> tuple[ProjectItem, str, bool]:
    """Resolve project and normalized path for memory:// identifiers.

    Returns:
        Tuple of (active_project, normalized_path, is_memory_url)
    """
    is_memory_url = identifier.strip().startswith("memory://")
    if not is_memory_url:
        active_project = await get_active_project(client, project, context, headers)
        return active_project, identifier, False

    normalized_path = normalize_project_reference(memory_url_path(identifier))
    project_prefix, remainder = _split_project_prefix(normalized_path)
    include_project = ConfigManager().config.permalinks_include_project

    # Trigger: memory URL begins with a potential project segment
    # Why: allow project-scoped memory URLs without requiring a separate project parameter
    # Outcome: attempt to resolve the prefix as a project and route to it
    if project_prefix:
        try:
            from basic_memory.mcp.tools.utils import call_post

            response = await call_post(
                client,
                "/v2/projects/resolve",
                json={"identifier": project_prefix},
                headers=headers,
            )
            resolved = ProjectResolveResponse.model_validate(response.json())
        except ToolError as exc:
            if "project not found" not in str(exc).lower():
                raise
        else:
            resolved_project = await resolve_project_parameter(project_prefix)
            if resolved_project and generate_permalink(resolved_project) != generate_permalink(
                project_prefix
            ):
                raise ValueError(
                    f"Project is constrained to '{resolved_project}', cannot use '{project_prefix}'."
                )

            active_project = ProjectItem(
                id=resolved.project_id,
                external_id=resolved.external_id,
                name=resolved.name,
                path=resolved.path,
                is_default=resolved.is_default,
            )
            if context:
                await context.set_state("active_project", active_project.model_dump())

            resolved_path = f"{resolved.permalink}/{remainder}" if include_project else remainder
            return active_project, resolved_path, True

    # Trigger: no resolvable project prefix in the memory URL
    # Why: preserve existing memory URL behavior within the active project
    # Outcome: use the active project and normalize the path for lookup
    active_project = await get_active_project(client, project, context, headers)
    resolved_path = normalized_path
    if include_project:
        # Trigger: project-prefixed permalinks are enabled and the path lacks a prefix
        # Why: ensure memory URL lookups align with canonical permalinks
        # Outcome: prefix the path with the active project's permalink
        project_prefix = active_project.permalink
        if resolved_path != project_prefix and not resolved_path.startswith(f"{project_prefix}/"):
            resolved_path = f"{project_prefix}/{resolved_path}"
    return active_project, resolved_path, True


def add_project_metadata(result: str, project_name: str) -> str:
    """Add project context as metadata footer for assistant session tracking.

    Provides clear project context to help the assistant remember which
    project is being used throughout the conversation session.

    Args:
        result: The tool result string
        project_name: The project name that was used

    Returns:
        Result with project session tracking metadata
    """
    return f"{result}\n\n[Session: Using project '{project_name}']"


def detect_project_from_url_prefix(identifier: str, config: BasicMemoryConfig) -> Optional[str]:
    """Check if a memory URL's first path segment matches a known project in config.

    This enables automatic project routing from memory URLs like
    ``memory://specs/in-progress`` without requiring the caller to pass
    an explicit ``project`` parameter.

    Uses local config only — no network calls.

    Args:
        identifier: Raw identifier string (may or may not start with ``memory://``).
        config: Current BasicMemoryConfig with project entries.

    Returns:
        Matching project name from config, or None if no match.
    """
    path = memory_url_path(identifier) if identifier.strip().startswith("memory://") else identifier
    normalized = normalize_project_reference(path)
    prefix, _ = _split_project_prefix(normalized)
    if prefix is None:
        return None

    prefix_permalink = generate_permalink(prefix)
    for project_name in config.projects:
        if generate_permalink(project_name) == prefix_permalink:
            return project_name
    return None


@asynccontextmanager
async def get_project_client(
    project: Optional[str] = None,
    workspace: Optional[str] = None,
    context: Optional[Context] = None,
) -> AsyncIterator[Tuple[AsyncClient, ProjectItem]]:
    """Resolve project, create correctly-routed client, and validate project.

    Solves the bootstrap problem: we need to know the project name to choose
    the right client (local vs cloud), but we need the client to validate
    the project. This helper resolves the project from config first (no
    network), creates the correctly-routed client, then validates via API.

    Routing decision order:
    1. Explicit --local/--cloud flags → skip workspace, use flag routing
    2. Cloud routing (explicit --cloud OR project mode CLOUD) →
       resolve workspace via priority chain, create cloud client
    3. Otherwise → local ASGI client

    Workspace resolution priority (when cloud routing):
    1. Explicit ``workspace`` parameter
    2. Per-project ``workspace_id`` from config
    3. Global ``default_workspace`` from config
    4. MCP session cache (context)
    5. Auto-select if single workspace
    6. Error listing choices

    Args:
        project: Optional explicit project parameter
        workspace: Optional cloud workspace selector (tenant_id or unique name)
        context: Optional FastMCP context for caching

    Yields:
        Tuple of (client, active_project)

    Raises:
        ValueError: If no project can be resolved
        RuntimeError: If cloud project but no API key configured
    """
    # Deferred imports to avoid circular dependency
    from basic_memory.mcp.async_client import (
        _explicit_routing,
        _force_local_mode,
        get_client,
    )

    # Step 1: Resolve project name from config (no network call)
    resolved_project = await resolve_project_parameter(project)
    if not resolved_project:
        # Fall back to local client to discover projects and raise helpful error
        async with get_client() as client:
            project_names = await get_project_names(client)
            raise ValueError(
                "No project specified. "
                "Either set 'default_project' in config, or use 'project' argument.\n"
                f"Available projects: {project_names}"
            )

    # Step 2: Check explicit routing BEFORE workspace resolution
    # Trigger: CLI passed --local or --cloud
    # Why: explicit flags must be deterministic — skip workspace entirely for --local
    # Outcome: route strictly based on explicit flag, no workspace network calls
    if _explicit_routing() and _force_local_mode():
        async with get_client(project_name=resolved_project) as client:
            active_project = await get_active_project(client, resolved_project, context)
            yield client, active_project
        return

    # Step 3: Determine if cloud routing is needed
    config = ConfigManager().config
    project_entry = config.projects.get(resolved_project)
    project_mode = config.get_project_mode(resolved_project)

    # Trigger: workspace provided for a local project (without explicit --cloud)
    # Why: workspace selection is a cloud routing concern only
    # Outcome: fail fast with a deterministic guidance message
    if project_mode != ProjectMode.CLOUD and workspace is not None and not _explicit_routing():
        raise ValueError(
            f"Workspace '{workspace}' cannot be used with local project '{resolved_project}'. "
            "Workspace selection is only supported for cloud-mode projects."
        )

    if project_mode == ProjectMode.CLOUD or (_explicit_routing() and not _force_local_mode()):
        # --- Cloud routing: resolve workspace with priority chain ---
        effective_workspace = workspace

        # Priority 2: per-project workspace_id from config
        if effective_workspace is None and project_entry and project_entry.workspace_id:
            effective_workspace = project_entry.workspace_id

        # Priority 3: global default_workspace from config
        if effective_workspace is None and config.default_workspace:
            effective_workspace = config.default_workspace

        # Priorities 4-6: if still unresolved, fall back to resolve_workspace_parameter
        # which checks context cache, auto-selects single workspace, or errors
        if effective_workspace is not None:
            # Config-resolved workspace — pass directly to get_client, skip network lookup
            async with get_client(
                project_name=resolved_project,
                workspace=effective_workspace,
            ) as client:
                active_project = await get_active_project(client, resolved_project, context)
                yield client, active_project
        else:
            # No config-based workspace — use resolve_workspace_parameter for discovery
            active_ws = await resolve_workspace_parameter(workspace=None, context=context)
            async with get_client(
                project_name=resolved_project,
                workspace=active_ws.tenant_id,
            ) as client:
                active_project = await get_active_project(client, resolved_project, context)
                yield client, active_project
        return

    # Step 4: Local routing (default)
    async with get_client(project_name=resolved_project) as client:
        active_project = await get_active_project(client, resolved_project, context)
        yield client, active_project
