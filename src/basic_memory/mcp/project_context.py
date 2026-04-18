"""Project context utilities for Basic Memory MCP server.

Provides project lookup utilities for MCP tools.
Handles project validation and context management in one place.

Note: This module uses ProjectResolver for unified project resolution.
The resolve_project_parameter function is a thin wrapper for backwards
compatibility with existing MCP tools.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional, List, Tuple, cast

from httpx import AsyncClient
from httpx._types import (
    HeaderTypes,
)
from loguru import logger
from fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

import logfire
from basic_memory.config import BasicMemoryConfig, ConfigManager, ProjectMode, has_cloud_credentials
from basic_memory.project_resolver import ProjectResolver
from basic_memory.schemas.cloud import WorkspaceInfo, WorkspaceListResponse
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.schemas.v2 import ProjectResolveResponse
from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import generate_permalink, normalize_project_reference

# --- Workspace provider injection ---
# Mirrors the set_client_factory() pattern in async_client.py.
# The cloud MCP server sets a provider that queries its own database directly,
# avoiding the control-plane HTTP round-trip that requires local credentials.
_workspace_provider: Optional[Callable[[], Awaitable[list[WorkspaceInfo]]]] = None
_WORKSPACE_PROJECT_INDEX_STATE_KEY = "workspace_project_index"


@dataclass(frozen=True)
class WorkspaceProjectEntry:
    """A cloud project resolved together with the workspace that owns it."""

    workspace: WorkspaceInfo
    project: ProjectItem

    @property
    def qualified_name(self) -> str:
        return f"{self.workspace.slug}/{self.project.permalink}"


@dataclass(frozen=True)
class WorkspaceProjectIndex:
    """Session-local cloud project lookup index keyed by project permalink."""

    workspaces: tuple[WorkspaceInfo, ...]
    entries: tuple[WorkspaceProjectEntry, ...]
    entries_by_permalink: dict[str, tuple[WorkspaceProjectEntry, ...]]
    failed_workspaces: tuple[WorkspaceInfo, ...] = ()


def set_workspace_provider(provider: Callable[[], Awaitable[list[WorkspaceInfo]]]) -> None:
    """Override workspace discovery (for cloud app, testing, etc)."""
    global _workspace_provider
    _workspace_provider = provider


async def _resolve_default_project_from_api() -> Optional[str]:
    """Query the projects API for the default project.

    Used as a fallback when ConfigManager has no local config (cloud mode).
    """
    from basic_memory.mcp.async_client import get_client

    try:
        async with get_client() as client:
            response = await client.get("/v2/projects/")
            if response.status_code == 200:
                project_list = ProjectList.model_validate(response.json())
                if project_list.default_project:
                    return project_list.default_project
                # Fallback: find project with is_default=True
                for p in project_list.projects:
                    if p.is_default:
                        return p.name
    except Exception:
        pass
    return None


async def _get_cached_active_project(context: Optional[Context]) -> Optional[ProjectItem]:
    """Return the cached active project from context when available."""
    if not context:
        return None

    cached_raw = await context.get_state("active_project")
    if isinstance(cached_raw, dict):
        return ProjectItem.model_validate(cached_raw)
    return None


async def _set_cached_active_project(
    context: Optional[Context],
    active_project: ProjectItem,
) -> None:
    """Persist the active project and known default-project metadata in context."""
    if not context:
        return

    await context.set_state("active_project", active_project.model_dump())
    if active_project.is_default:
        await context.set_state("default_project_name", active_project.name)


async def _get_cached_active_workspace(context: Optional[Context]) -> Optional[WorkspaceInfo]:
    """Return the cached active workspace from context when available."""
    if not context:
        return None

    cached_raw = await context.get_state("active_workspace")
    if isinstance(cached_raw, dict):
        return WorkspaceInfo.model_validate(cached_raw)
    return None


async def _set_cached_active_workspace(
    context: Optional[Context],
    active_workspace: WorkspaceInfo,
) -> None:
    """Persist workspace context and clear project cache when the tenant changes."""
    if not context:
        return

    cached_workspace = await _get_cached_active_workspace(context)
    if cached_workspace and cached_workspace.tenant_id != active_workspace.tenant_id:
        # Trigger: project routing moved to another workspace
        # Why: project names are only unique inside one workspace, so a cached
        #   ProjectItem from the previous tenant can point at the wrong project
        # Outcome: force the next validation call to resolve within the new tenant
        await context.set_state("active_project", None)
        await context.set_state("default_project_name", None)

    await context.set_state("active_workspace", active_workspace.model_dump())


async def _get_cached_default_project(context: Optional[Context]) -> Optional[str]:
    """Return the cached default project name from context when available."""
    if not context:
        return None

    cached_default = await context.get_state("default_project_name")
    if isinstance(cached_default, str):
        return cached_default
    return None


def _canonicalize_project_name(
    project_name: Optional[str],
    config: BasicMemoryConfig,
) -> Optional[str]:
    """Return the configured project name when the identifier matches by permalink.

    Project routing happens before API validation, so we normalize explicit inputs
    here to keep local/cloud routing aligned with the database's case-insensitive
    project resolver.
    """
    if project_name is None:
        return None

    requested_permalink = generate_permalink(project_name)
    for configured_name in config.projects:
        if generate_permalink(configured_name) == requested_permalink:
            return configured_name

    return project_name


def _project_matches_identifier(project_item: ProjectItem, identifier: Optional[str]) -> bool:
    """Return True when the identifier refers to the cached project."""
    if identifier is None:
        return True

    normalized_identifier = generate_permalink(identifier)
    return normalized_identifier in {
        generate_permalink(project_item.name),
        project_item.permalink,
    }


async def resolve_project_parameter(
    project: Optional[str] = None,
    allow_discovery: bool = False,
    default_project: Optional[str] = None,
    context: Optional[Context] = None,
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
    with logfire.span(
        "routing.resolve_project",
        requested_project=project,
        allow_discovery=allow_discovery,
    ):
        config = ConfigManager().config

        # Trigger: project already resolved earlier in the same MCP request
        # Why: the active project is request-constant, so re-discovering the
        #   default project via /v2/projects/ just repeats work
        # Outcome: reuse the cached project name as the explicit candidate
        if project is None:
            cached_project = await _get_cached_active_project(context)
            if cached_project is not None:
                project = cached_project.name

        # Trigger: there is no explicit project after env/context normalization
        # Why: default-project discovery is only needed as a fallback; doing it
        #   for explicit requests adds an avoidable /v2/projects/ round-trip
        # Outcome: skip default lookup when the active project is already known
        if default_project is None and project is None:
            # Load config for any values not explicitly provided.
            # ConfigManager reads from the local config file, which doesn't exist in cloud mode.
            # When it returns None, fall back to querying the projects API for the is_default flag.
            default_project = config.default_project

            if default_project is None:
                default_project = await _get_cached_default_project(context)

            if default_project is None:
                default_project = await _resolve_default_project_from_api()
                if default_project and context:
                    await context.set_state("default_project_name", default_project)

        # Create resolver with configuration and resolve
        resolver = ProjectResolver.from_env(
            default_project=default_project,
        )
        result = resolver.resolve(project=project, allow_discovery=allow_discovery)
        return _canonicalize_project_name(result.project, config)


async def get_project_names(client: AsyncClient, headers: HeaderTypes | None = None) -> List[str]:
    # Deferred import to avoid circular dependency with tools
    from basic_memory.mcp.tools.utils import call_get

    response = await call_get(client, "/v2/projects/", headers=headers)
    project_list = ProjectList.model_validate(response.json())
    return [project.name for project in project_list.projects]


def _workspace_matches_identifier(workspace: WorkspaceInfo, identifier: str) -> bool:
    """Return True when identifier matches workspace tenant_id, slug, or name."""
    if workspace.tenant_id == identifier:
        return True
    if workspace.slug.casefold() == identifier.casefold():
        return True
    return workspace.name.lower() == identifier.lower()


def _workspace_choices(workspaces: list[WorkspaceInfo]) -> str:
    """Format deterministic workspace choices for prompt-style errors."""
    return "\n".join(
        [
            (
                f"- {item.name} "
                f"(slug={item.slug}, type={item.workspace_type}, "
                f"role={item.role}, tenant_id={item.tenant_id})"
            )
            for item in workspaces
        ]
    )


def _workspace_project_index_from_state(raw: object) -> WorkspaceProjectIndex | None:
    """Deserialize a cached workspace project index from MCP context state."""
    if not isinstance(raw, dict):
        return None

    raw_mapping = cast(dict[str, object], raw)
    workspaces_raw = raw_mapping.get("workspaces")
    entries_raw = raw_mapping.get("entries")
    if not isinstance(workspaces_raw, list) or not isinstance(entries_raw, list):
        return None

    workspaces = tuple(WorkspaceInfo.model_validate(item) for item in workspaces_raw)
    failed_workspaces_raw = raw_mapping.get("failed_workspaces")
    failed_workspaces = (
        tuple(WorkspaceInfo.model_validate(item) for item in failed_workspaces_raw)
        if isinstance(failed_workspaces_raw, list)
        else ()
    )
    entries_list: list[WorkspaceProjectEntry] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            continue
        item_mapping = cast(dict[str, object], item)
        workspace_raw = item_mapping.get("workspace")
        project_raw = item_mapping.get("project")
        if workspace_raw is None or project_raw is None:
            continue
        entries_list.append(
            WorkspaceProjectEntry(
                workspace=WorkspaceInfo.model_validate(workspace_raw),
                project=ProjectItem.model_validate(project_raw),
            )
        )
    entries = tuple(entries_list)
    return _build_workspace_project_index(
        workspaces,
        entries,
        failed_workspaces=failed_workspaces,
    )


def _workspace_project_index_to_state(index: WorkspaceProjectIndex) -> dict:
    """Serialize a workspace project index for MCP context state."""
    return {
        "workspaces": [workspace.model_dump() for workspace in index.workspaces],
        "failed_workspaces": [workspace.model_dump() for workspace in index.failed_workspaces],
        "entries": [
            {
                "workspace": entry.workspace.model_dump(),
                "project": entry.project.model_dump(),
            }
            for entry in index.entries
        ],
    }


def _build_workspace_project_index(
    workspaces: tuple[WorkspaceInfo, ...],
    entries: tuple[WorkspaceProjectEntry, ...],
    *,
    failed_workspaces: tuple[WorkspaceInfo, ...] = (),
) -> WorkspaceProjectIndex:
    """Build the permalink lookup table for workspace-project entries."""
    grouped: dict[str, list[WorkspaceProjectEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.project.permalink, []).append(entry)

    return WorkspaceProjectIndex(
        workspaces=workspaces,
        entries=entries,
        entries_by_permalink={
            permalink: tuple(items)
            for permalink, items in sorted(grouped.items(), key=lambda item: item[0])
        },
        failed_workspaces=failed_workspaces,
    )


def _split_qualified_project_identifier(identifier: str) -> tuple[str | None, str]:
    """Split ``<workspace-slug>/<project>`` identifiers for cloud routing."""
    cleaned = identifier.strip()
    if "/" not in cleaned:
        return None, cleaned

    workspace_slug, project_identifier = cleaned.split("/", 1)
    if not workspace_slug or not project_identifier:
        return None, cleaned
    return workspace_slug, project_identifier


def _unqualified_project_identifier(identifier: str) -> str:
    """Return the project segment from an optional qualified project identifier."""
    _, project_identifier = _split_qualified_project_identifier(identifier)
    return project_identifier


def _format_qualified_choices(entries: tuple[WorkspaceProjectEntry, ...]) -> str:
    """Format qualified project choices for collision errors."""
    return " or ".join(entry.qualified_name for entry in entries)


async def get_available_workspaces(context: Optional[Context] = None) -> list[WorkspaceInfo]:
    """Load available cloud workspaces for the current authenticated user."""
    if context:
        cached_raw = await context.get_state("available_workspaces")
        if isinstance(cached_raw, list):
            return [WorkspaceInfo.model_validate(item) for item in cached_raw]

    # Trigger: workspace provider was injected (e.g., by cloud MCP server)
    # Why: the cloud server IS the cloud — it can query its own database
    #   directly instead of making an HTTP round-trip that requires local credentials
    # Outcome: use provider result, cache in context, skip control-plane client
    if _workspace_provider is not None:
        workspaces = await _workspace_provider()
        if context:
            await context.set_state(
                "available_workspaces",
                [ws.model_dump() for ws in workspaces],
            )
        return workspaces

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


async def invalidate_workspace_project_index(context: Optional[Context] = None) -> None:
    """Invalidate the cached cloud workspace/project lookup index."""
    if context:
        await context.set_state(_WORKSPACE_PROJECT_INDEX_STATE_KEY, None)


async def _fetch_workspace_project_entries(
    workspace: WorkspaceInfo,
    context: Optional[Context] = None,
) -> tuple[WorkspaceProjectEntry, ...]:
    """Fetch projects for one workspace and tag each project with workspace metadata."""
    from basic_memory.mcp.async_client import get_client, get_cloud_proxy_client, is_factory_mode
    from basic_memory.mcp.clients import ProjectClient

    client_context = (
        get_client(workspace=workspace.tenant_id)
        if is_factory_mode()
        else get_cloud_proxy_client(workspace=workspace.tenant_id)
    )

    async with client_context as client:
        project_list = await ProjectClient(client).list_projects()

    default_permalink = (
        generate_permalink(project_list.default_project) if project_list.default_project else None
    )
    entries: list[WorkspaceProjectEntry] = []
    for project in project_list.projects:
        entry_project = project
        if default_permalink and project.permalink == default_permalink and not project.is_default:
            entry_project = project.model_copy(update={"is_default": True})
        entries.append(WorkspaceProjectEntry(workspace=workspace, project=entry_project))

    if context:  # pragma: no cover
        await context.info(
            f"Discovered {len(entries)} cloud projects in workspace {workspace.slug}"
        )

    return tuple(entries)


async def _ensure_workspace_project_index(
    context: Optional[Context] = None,
) -> WorkspaceProjectIndex:
    """Build or load the session-local workspace/project lookup index."""
    if context:
        cached_raw = await context.get_state(_WORKSPACE_PROJECT_INDEX_STATE_KEY)
        cached_index = _workspace_project_index_from_state(cached_raw)
        if cached_index is not None:
            return cached_index

    workspaces = tuple(await get_available_workspaces(context=context))
    if not workspaces:
        raise ValueError(
            "No accessible workspaces found for this account. "
            "Ensure you have an active subscription and tenant access."
        )

    fetched_results = await asyncio.gather(
        *[_fetch_workspace_project_entries(workspace, context=context) for workspace in workspaces],
        return_exceptions=True,
    )
    entries_list: list[WorkspaceProjectEntry] = []
    failed_workspaces: list[WorkspaceInfo] = []
    successful_fetches = 0
    for workspace, result in zip(workspaces, fetched_results, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result
            # Trigger: one workspace project listing failed during a multi-workspace index.
            # Why: a transient or unauthorized tenant should not break qualified routing for
            #   healthy workspaces, but unqualified routing still needs to know the index is partial.
            # Outcome: keep successful workspace entries and record the failed workspace.
            failed_workspaces.append(workspace)
            logger.warning(
                f"Cloud project discovery failed for workspace {workspace.slug} "
                f"({workspace.tenant_id}): {result}"
            )
            if context:  # pragma: no cover
                await context.info(
                    f"Cloud project discovery failed for workspace {workspace.slug}; "
                    "continuing with other workspaces"
                )
            continue

        workspace_entries = cast(tuple[WorkspaceProjectEntry, ...], result)
        successful_fetches += 1
        entries_list.extend(workspace_entries)

    if failed_workspaces and successful_fetches == 0:
        failed_labels = ", ".join(workspace.slug for workspace in failed_workspaces)
        raise ValueError(
            "Unable to discover projects in any accessible workspace. "
            f"Failed workspaces: {failed_labels}"
        )

    entries = tuple(entries_list)
    index = _build_workspace_project_index(
        workspaces,
        entries,
        failed_workspaces=tuple(failed_workspaces),
    )

    if context:
        await context.set_state(
            _WORKSPACE_PROJECT_INDEX_STATE_KEY,
            _workspace_project_index_to_state(index),
        )

    return index


async def ensure_workspace_project_index(
    context: Optional[Context] = None,
) -> WorkspaceProjectIndex:
    """Public wrapper for loading the session-local workspace/project lookup index."""
    return await _ensure_workspace_project_index(context=context)


async def resolve_workspace_project_identifier(
    project: str,
    context: Optional[Context] = None,
) -> WorkspaceProjectEntry:
    """Resolve an unqualified or ``<workspace>/<project>`` cloud project identifier."""
    index = await _ensure_workspace_project_index(context=context)
    workspace_slug, project_identifier = _split_qualified_project_identifier(project)
    project_permalink = generate_permalink(project_identifier)

    if workspace_slug:
        workspace_matches = [
            workspace
            for workspace in index.workspaces
            if workspace.slug.casefold() == workspace_slug.casefold()
        ]
        if not workspace_matches:
            available = ", ".join(workspace.slug for workspace in index.workspaces)
            raise ValueError(
                f"Workspace '{workspace_slug}' was not found. "
                f"Available workspace slugs: {available}"
            )

        workspace = workspace_matches[0]
        matches = [
            entry
            for entry in index.entries_by_permalink.get(project_permalink, ())
            if entry.workspace.tenant_id == workspace.tenant_id
        ]
        if not matches:
            if any(
                failed_workspace.tenant_id == workspace.tenant_id
                for failed_workspace in index.failed_workspaces
            ):
                raise ValueError(
                    f"Projects for workspace '{workspace.name}' ({workspace.slug}) "
                    "could not be loaded. Retry after workspace discovery recovers."
                )
            available = ", ".join(
                entry.qualified_name
                for entry in index.entries
                if entry.workspace.tenant_id == workspace.tenant_id
            )
            raise ValueError(
                f"Project '{project_identifier}' was not found in workspace "
                f"'{workspace.name}' ({workspace.slug}). Available projects: {available}"
            )
        return matches[0]

    matches = index.entries_by_permalink.get(project_permalink, ())
    if not matches:
        failed_note = ""
        if index.failed_workspaces:
            failed = ", ".join(workspace.slug for workspace in index.failed_workspaces)
            failed_note = (
                f" Project discovery failed for workspace(s): {failed}; "
                "retry or use a qualified project from an indexed workspace."
            )
        available = ", ".join(entry.qualified_name for entry in index.entries)
        raise ValueError(
            f"Project '{project}' was not found in indexed cloud workspaces. "
            f"Available projects: {available}.{failed_note}"
        )

    cached_workspace = await _get_cached_active_workspace(context)
    if cached_workspace:
        cached_matches = [
            entry for entry in matches if entry.workspace.tenant_id == cached_workspace.tenant_id
        ]
        if cached_matches:
            return cached_matches[0]

    if len(matches) > 1:
        choices = _format_qualified_choices(matches)
        details = "\n".join(
            f"- {entry.workspace.name} ({entry.workspace.slug}): {entry.qualified_name}"
            for entry in matches
        )
        raise ValueError(
            f"Project '{project}' exists in multiple workspaces. Use: {choices}\n{details}"
        )

    if index.failed_workspaces:
        qualified_name = matches[0].qualified_name
        failed = ", ".join(workspace.slug for workspace in index.failed_workspaces)
        raise ValueError(
            f"Project '{project}' was found as {qualified_name}, but project discovery "
            f"failed for workspace(s): {failed}. Use '{qualified_name}' to route "
            "explicitly, or retry after discovery recovers."
        )

    return matches[0]


async def _default_workspace_project_entry(
    context: Optional[Context] = None,
) -> WorkspaceProjectEntry | None:
    """Return the default project from the default cloud workspace, when available."""
    index = await _ensure_workspace_project_index(context=context)
    default_workspace = next(
        (workspace for workspace in index.workspaces if workspace.is_default),
        None,
    )
    if default_workspace is None:
        return None

    default_entries = [
        entry
        for entry in index.entries
        if entry.workspace.tenant_id == default_workspace.tenant_id and entry.project.is_default
    ]
    return default_entries[0] if default_entries else None


async def resolve_workspace_parameter(
    workspace: Optional[str] = None,
    context: Optional[Context] = None,
) -> WorkspaceInfo:
    """Resolve workspace using explicit input, session cache, and cloud discovery."""
    with logfire.span(
        "routing.resolve_workspace",
        workspace_requested=workspace is not None,
        has_context=context is not None,
    ):
        if context:
            cached_raw = await context.get_state("active_workspace")
            if isinstance(cached_raw, dict):
                cached_workspace = WorkspaceInfo.model_validate(cached_raw)
                if workspace is None or _workspace_matches_identifier(cached_workspace, workspace):
                    logger.debug(
                        f"Using cached workspace from context: {cached_workspace.tenant_id}"
                    )
                    return cached_workspace

        workspaces = await get_available_workspaces(context=context)
        if not workspaces:
            raise ValueError(
                "No accessible workspaces found for this account. "
                "Ensure you have an active subscription and tenant access."
            )

        selected_workspace: WorkspaceInfo | None = None

        if workspace:
            matches = [
                item for item in workspaces if _workspace_matches_identifier(item, workspace)
            ]
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

        await _set_cached_active_workspace(context, selected_workspace)
        if context:
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
    with logfire.span(
        "routing.validate_project",
        requested_project=project,
        has_context=context is not None,
    ):
        # Deferred import to avoid circular dependency with tools
        from basic_memory.mcp.tools.utils import call_post

        cached_project = await _get_cached_active_project(context)
        if cached_project and _project_matches_identifier(cached_project, project):
            logger.debug(f"Using cached project from context: {cached_project.name}")
            return cached_project

        resolved_project = await resolve_project_parameter(project, context=context)
        if not resolved_project:
            project_names = await get_project_names(client, headers)
            raise ValueError(
                "No project specified. "
                "Either set 'default_project' in config, or use 'project' argument.\n"
                f"Available projects: {project_names}"
            )

        project = resolved_project

        if cached_project and _project_matches_identifier(cached_project, project):
            logger.debug(f"Using cached project from context: {cached_project.name}")
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
        await _set_cached_active_project(context, active_project)
        if context:
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
    config = ConfigManager().config
    include_project = config.permalinks_include_project if is_memory_url else None
    with logfire.span(
        "routing.resolve_memory_url",
        is_memory_url=is_memory_url,
        requested_project=project,
        include_project_prefix=include_project,
    ):
        if not is_memory_url:
            active_project = await get_active_project(client, project, context, headers)
            return active_project, identifier, False

        normalized_path = normalize_project_reference(memory_url_path(identifier))
        project_prefix, remainder = _split_project_prefix(normalized_path)
        include_project = config.permalinks_include_project
        # Trigger: memory URL begins with a potential project segment
        # Why: allow project-scoped memory URLs without requiring a separate project parameter
        # Outcome: attempt to resolve the prefix as a project and route to it
        if project_prefix:
            cached_project = await _get_cached_active_project(context)
            if cached_project and _project_matches_identifier(cached_project, project_prefix):
                resolved_project = await resolve_project_parameter(project_prefix, context=context)
                if resolved_project and generate_permalink(resolved_project) != generate_permalink(
                    project_prefix
                ):
                    raise ValueError(
                        f"Project is constrained to '{resolved_project}', cannot use '{project_prefix}'."
                    )

                resolved_path = (
                    f"{cached_project.permalink}/{remainder}" if include_project else remainder
                )
                return cached_project, resolved_path, True

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
                resolved_project = await resolve_project_parameter(project_prefix, context=context)
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
                await _set_cached_active_project(context, active_project)

                resolved_path = (
                    f"{resolved.permalink}/{remainder}" if include_project else remainder
                )
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
            if resolved_path != project_prefix and not resolved_path.startswith(
                f"{project_prefix}/"
            ):
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
    1. Explicit --local flag → skip workspace, use local routing
    2. Factory/cloud routing → resolve project through workspace/project index
    3. Cloud project mode → resolve project through workspace/project index
    4. Otherwise → local ASGI client

    Workspace resolution priority (when cloud routing):
    1. Explicit ``workspace`` parameter
    2. Per-project ``workspace_id`` from config
    3. Qualified project identifier (``<workspace-slug>/<project>``)
    4. Workspace/project index lookup with collision detection

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
        is_factory_mode,
    )

    # Step 1: Resolve project name from config (no network call)
    resolved_project = await resolve_project_parameter(project, context=context)
    config = ConfigManager().config
    factory_mode = is_factory_mode()
    explicit_cloud_routing = _explicit_routing() and not _force_local_mode()
    cloud_default_entry: WorkspaceProjectEntry | None = None

    if (
        resolved_project is None
        and not (_explicit_routing() and _force_local_mode())
        and (
            factory_mode
            or explicit_cloud_routing
            or (not config.projects and has_cloud_credentials(config))
        )
    ):
        cloud_default_entry = await _default_workspace_project_entry(context=context)
        if cloud_default_entry is not None:
            resolved_project = cloud_default_entry.project.name
            await _set_cached_active_workspace(context, cloud_default_entry.workspace)

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
        route_mode = "explicit_local"
        with logfire.span(
            "routing.client_session",
            project_name=resolved_project,
            route_mode=route_mode,
        ):
            logger.debug("Explicit local routing selected for project client")
            async with get_client(project_name=resolved_project) as client:
                active_project = await get_active_project(client, resolved_project, context)
                yield client, active_project
        return

    # Step 3: Determine if cloud routing is needed
    project_entry = config.projects.get(resolved_project)
    project_mode = config.get_project_mode(resolved_project)

    # Trigger: workspace provided for a local project (without explicit --cloud)
    # Why: workspace selection is a cloud routing concern only
    # Outcome: fail fast with a deterministic guidance message
    if (
        not factory_mode
        and project_mode != ProjectMode.CLOUD
        and workspace is not None
        and not _explicit_routing()
    ):
        raise ValueError(
            f"Workspace '{workspace}' cannot be used with local project '{resolved_project}'. "
            "Workspace selection is only supported for cloud-mode projects."
        )

    if factory_mode or project_mode == ProjectMode.CLOUD or explicit_cloud_routing:
        route_mode = "factory" if factory_mode else "cloud_proxy"
        active_ws: WorkspaceInfo | None = None
        workspace_id: str
        project_for_api = _unqualified_project_identifier(resolved_project)

        # Trigger: a script or config entry pins the tenant explicitly
        # Why: explicit tenant configuration remains the escape hatch during migration
        # Outcome: route to that workspace, but validate the project name inside it
        if workspace is not None:
            active_ws = await resolve_workspace_parameter(workspace=workspace, context=context)
            workspace_id = active_ws.tenant_id
        elif project_entry and project_entry.workspace_id:
            # Trigger: the local project config already stores the cloud tenant id.
            # Why: routing can send that id directly; requiring workspace discovery here
            #   would turn a control-plane listing outage into a project routing failure.
            # Outcome: preserve project-scoped routing even when discovery is unavailable.
            workspace_id = project_entry.workspace_id
        else:
            resolved_entry = cloud_default_entry
            if resolved_entry is None or not _project_matches_identifier(
                resolved_entry.project, resolved_project
            ):
                resolved_entry = await resolve_workspace_project_identifier(
                    resolved_project,
                    context=context,
                )
            active_ws = resolved_entry.workspace
            workspace_id = active_ws.tenant_id
            project_for_api = resolved_entry.project.name

        if active_ws is not None:
            await _set_cached_active_workspace(context, active_ws)
        with logfire.span(
            "routing.client_session",
            project_name=project_for_api,
            route_mode=route_mode,
            workspace_id=workspace_id,
        ):
            logger.debug("Using resolved workspace for cloud project routing")
            async with get_client(
                project_name=project_for_api,
                workspace=workspace_id,
            ) as client:
                active_project = await get_active_project(client, project_for_api, context)
                yield client, active_project
        return

    # Step 4: Local routing (default)
    route_mode = "local_asgi"
    with logfire.span(
        "routing.client_session",
        project_name=resolved_project,
        route_mode=route_mode,
    ):
        logger.debug("Using default local ASGI routing for project client")
        async with get_client(project_name=resolved_project) as client:
            active_project = await get_active_project(client, resolved_project, context)
            yield client, active_project
