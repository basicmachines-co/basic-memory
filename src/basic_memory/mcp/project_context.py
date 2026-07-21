"""Project context utilities for Basic Memory MCP server.

Provides project lookup utilities for MCP tools.
Handles project validation and context management in one place.

Note: This module uses ProjectResolver for unified project resolution.
The resolve_project_parameter function is a thin wrapper for backwards
compatibility with existing MCP tools.
"""

# PEP 563 lazy annotations keep `Context` usable in signatures without importing
# fastmcp at module load — the fastmcp/mcp stack costs ~0.5s of CLI startup (#886).
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from typing import (
    TYPE_CHECKING,
    AsyncIterator,
    Awaitable,
    Callable,
    List,
    Optional,
    Tuple,
)
from uuid import UUID

from httpx import AsyncClient
from httpx._types import (
    HeaderTypes,
)
from loguru import logger

import logfire
from basic_memory.config import BasicMemoryConfig, ConfigManager, ProjectMode, has_cloud_credentials
from basic_memory.project_resolver import ProjectResolver
from basic_memory.schemas.cloud import (
    WorkspaceInfo,
    WorkspaceListResponse,
    format_workspace_choices,
    format_workspace_selection_choices,
    workspace_matches_exact_identifier,
    workspace_matches_identifier,
)
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.schemas.v2 import ProjectResolveResponse
from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import generate_permalink, normalize_project_reference
from basic_memory.workspace_context import (
    current_workspace_permalink_context,
    workspace_permalink_context,
)
from basic_memory.mcp.project_context_identifiers import (
    UnresolvedProjectRouteError,
    WorkspaceMemoryUrlResolution,
    add_project_metadata as _add_project_metadata,
    canonical_memory_path_for_active_route as _canonical_memory_path_for_active_route,
    canonical_memory_path_for_workspace as _canonical_memory_path_for_workspace,
    canonicalize_project_name as _canonicalize_project_name,
    detect_project_from_url_prefix,
    identifier_path as _identifier_path,
    project_matches_identifier as _project_matches_identifier,
    split_project_prefix as _split_project_prefix,
    split_qualified_project_identifier as _split_qualified_project_identifier_impl,
    split_workspace_identifier_segments as _split_workspace_identifier_segments,
    split_workspace_memory_url_segments as _split_workspace_memory_url_segments,
    unqualified_project_identifier as _unqualified_project_identifier,
)
from basic_memory.mcp.workspace_project_index import (
    WORKSPACE_PROJECT_INDEX_STATE_KEY as _WORKSPACE_PROJECT_INDEX_STATE_KEY,
    WorkspaceProjectEntry,
    WorkspaceProjectIndex,
    WorkspaceProjectLookupMiss,
    build_workspace_project_index as _build_workspace_project_index,
    clear_cached_active_project as _clear_cached_active_project,
    clear_cached_active_workspace_for_local_route as _clear_cached_active_workspace_for_local_route,
    format_qualified_choices as _format_qualified_choices_impl,
    get_cached_active_project as _get_cached_active_project,
    get_cached_active_workspace as _get_cached_active_workspace,
    get_cached_default_project as _get_cached_default_project,
    match_workspace_identifier as _match_workspace_identifier_impl,
    resolve_workspace_project_from_index as _resolve_workspace_project_from_index,
    set_cached_active_project as _set_cached_active_project,
    set_cached_active_workspace as _set_cached_active_workspace,
    workspace_project_index_from_state as _workspace_project_index_from_state,
    workspace_project_index_to_state as _workspace_project_index_to_state,
)

# Keep the original module's helper surface intact for callers and tests while
# the implementations live in focused, dependency-light modules.
add_project_metadata = _add_project_metadata
_format_qualified_choices = _format_qualified_choices_impl
_match_workspace_identifier = _match_workspace_identifier_impl
_split_qualified_project_identifier = _split_qualified_project_identifier_impl

if TYPE_CHECKING:
    from fastmcp import Context

# --- Workspace provider injection ---
# Mirrors the set_client_factory() pattern in async_client.py.
# The cloud MCP server sets a provider that queries its own database directly,
# avoiding the control-plane HTTP round-trip that requires local credentials.
_workspace_provider: Optional[Callable[[], Awaitable[list[WorkspaceInfo]]]] = None


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


def _cloud_workspace_discovery_available(config: BasicMemoryConfig) -> bool:
    """Return True when workspace discovery can be used without forcing local routing."""
    from basic_memory.mcp.async_client import (
        _explicit_routing,
        _force_local_mode,
        is_factory_mode,
    )

    if _explicit_routing() and _force_local_mode():
        return False

    # Trigger: local project config is present even though cloud credentials are saved.
    # Why: existing local `memory://...` URLs must not depend on workspace discovery.
    # Outcome: only factory, explicit cloud, or cloud-only sessions attempt discovery here.
    return (
        is_factory_mode()
        or (_explicit_routing() and not _force_local_mode())
        or (not config.projects and has_cloud_credentials(config))
    )


def _workspace_identifier_discovery_available(
    identifier: str,
    config: BasicMemoryConfig,
) -> bool:
    """Return True when an identifier is allowed to consult workspace discovery."""
    if _cloud_workspace_discovery_available(config):
        return True

    from basic_memory.mcp.async_client import (
        _explicit_routing,
        _force_local_mode,
    )

    if _explicit_routing() and _force_local_mode():
        return False

    return (
        has_cloud_credentials(config)
        and _split_workspace_identifier_segments(identifier) is not None
    )


async def resolve_workspace_qualified_memory_url(
    identifier: str,
    context: Optional[Context] = None,
) -> WorkspaceMemoryUrlResolution | None:
    """Resolve a workspace-qualified memory URL against accessible workspaces."""
    segments = _split_workspace_memory_url_segments(identifier)
    if segments is None:
        return None

    return await _resolve_workspace_segments(identifier, segments, context=context)


async def resolve_workspace_qualified_identifier(
    identifier: str,
    context: Optional[Context] = None,
) -> WorkspaceMemoryUrlResolution | None:
    """Resolve a workspace-qualified permalink or memory URL against accessible workspaces."""
    segments = _split_workspace_identifier_segments(identifier)
    if segments is None:
        return None

    return await _resolve_workspace_segments(identifier, segments, context=context)


async def _resolve_workspace_segments(
    identifier: str,
    segments: tuple[str, str, str],
    context: Optional[Context] = None,
) -> WorkspaceMemoryUrlResolution | None:
    """Resolve parsed workspace/project/path segments against accessible workspaces."""
    workspace_slug, project_identifier, remainder = segments
    index = await _ensure_workspace_project_index(context=context)
    workspace = next(
        (item for item in index.workspaces if item.slug.casefold() == workspace_slug.casefold()),
        None,
    )
    if workspace is None:
        return None

    project_permalink = generate_permalink(project_identifier)
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

        # Trigger: first segment matches a workspace slug but the second does not
        #   match a project in that workspace.
        # Why: workspace-qualified URLs require both route segments to match; otherwise
        #   existing project-prefixed URLs like `memory://main/notes/foo` can collide
        #   with a workspace slug named `main`.
        # Outcome: treat this as not workspace-qualified and let the caller use
        #   the existing project-prefix/default-project resolver.
        return None
    if len(matches) > 1:
        details = ", ".join(
            f"{entry.qualified_name} ({entry.project.external_id})" for entry in matches
        )
        raise ValueError(
            f"Project '{project_identifier}' matched multiple projects in workspace "
            f"'{workspace.name}' ({workspace.slug}). Project permalinks must be unique. "
            f"Matches: {details}"
        )

    entry = matches[0]
    canonical_path = _canonical_memory_path_for_workspace(
        workspace_slug=entry.workspace.slug,
        workspace_type=entry.workspace.workspace_type,
        project_permalink=entry.project.permalink,
        remainder=remainder,
    )
    return WorkspaceMemoryUrlResolution(entry=entry, canonical_path=canonical_path)


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


async def invalidate_project_caches(context: Optional[Context] = None) -> None:
    """Invalidate project identity caches after a project lifecycle change."""
    await _clear_cached_active_project(context)
    await invalidate_workspace_project_index(context)


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
    *,
    force_refresh: bool = False,
) -> WorkspaceProjectIndex:
    """Build or load the session-local workspace/project lookup index.

    force_refresh bypasses the cached index and rebuilds from discovery —
    used by resolve_workspace_project_identifier when a lookup misses (#956).
    """
    if context and not force_refresh:
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

        workspace_entries = result
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
    """Resolve a project by external_id (UUID), qualified name, or unqualified name."""
    index = await _ensure_workspace_project_index(context=context)
    try:
        return await _resolve_workspace_project_from_index(index, project, context)
    except WorkspaceProjectLookupMiss:
        # Trigger: the lookup missed the session-cached index.
        # Why: a miss is exactly the signal the cache may be stale — projects
        #   created out-of-band (CLI, a teammate in a shared workspace) post-date
        #   the index built at session start (#956).
        # Outcome: rebuild the index once and retry; a second miss is authoritative
        #   and its error (with the refreshed project list) propagates.
        logger.info(
            f"Workspace project lookup missed for '{project}'; refreshing index and retrying"
        )
        refreshed = await _ensure_workspace_project_index(context=context, force_refresh=True)
        return await _resolve_workspace_project_from_index(refreshed, project, context)


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


async def _workspace_metadata_by_tenant_id(
    tenant_id: str,
    context: Optional[Context] = None,
) -> WorkspaceInfo | None:
    """Return non-index workspace metadata for a configured tenant id."""
    cached_workspace = await _get_cached_active_workspace(context)
    if cached_workspace and cached_workspace.tenant_id == tenant_id:
        return cached_workspace

    if cached_workspace and context:
        # Trigger: the configured workspace_id differs from cached workspace metadata.
        # Why: tenant_id routes the request, but stale workspace slug/type would corrupt
        #   memory URL normalization and canonical permalink headers.
        # Outcome: drop stale metadata and route without permalink decoration.
        await context.set_state("active_workspace", None)

    if context:
        cached_raw = await context.get_state("available_workspaces")
        if isinstance(cached_raw, list):
            for item in cached_raw:
                if not isinstance(item, dict):
                    continue
                workspace = WorkspaceInfo.model_validate(item)
                if workspace.tenant_id == tenant_id:
                    return workspace

    if _workspace_provider is not None:
        # Trigger: the hosting runtime can provide workspace metadata directly.
        # Why: configured workspace_id is already sufficient for tenant routing, but
        #   canonical organization permalinks also need slug/type context.
        # Outcome: use the injected runtime seam without loading the workspace project index.
        workspace = next(
            (
                workspace
                for workspace in await get_available_workspaces(context=context)
                if workspace.tenant_id == tenant_id
            ),
            None,
        )
        if workspace is None:
            raise ValueError(
                f"Configured workspace_id '{tenant_id}' was not returned by the workspace "
                "metadata provider. Reconfigure the project workspace or retry after "
                "workspace metadata recovers."
            )
        return workspace

    return None


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
                if workspace is None or workspace_matches_exact_identifier(
                    cached_workspace, workspace
                ):
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
            matches = [item for item in workspaces if workspace_matches_identifier(item, workspace)]
            if not matches:
                raise ValueError(
                    f"Workspace '{workspace}' was not found.\n"
                    f"Available workspaces:\n{format_workspace_choices(workspaces)}"
                )
            if len(matches) > 1:
                raise ValueError(
                    f"Workspace '{workspace}' matches multiple workspaces. "
                    "Choose one of these matching workspaces by slug or tenant_id:\n"
                    f"{format_workspace_selection_choices(matches)}"
                )
            selected_workspace = matches[0]
        elif len(workspaces) == 1:
            selected_workspace = workspaces[0]
        else:
            raise ValueError(
                "Multiple workspaces are available. Ask the user which workspace to use, then retry "
                "with the 'workspace' argument set to the tenant_id or unique name/slug/type.\n"
                f"Available workspaces:\n{format_workspace_choices(workspaces)}"
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


async def resolve_project_and_path(
    client: AsyncClient,
    identifier: str,
    project: Optional[str] = None,
    context: Optional[Context] = None,
    headers: HeaderTypes | None = None,
    *,
    strict_project_routing: bool = False,
    allow_missing_project_fallback: bool = False,
    cache_resolved_project: bool = True,
) -> tuple[ProjectItem, str, bool]:
    """Resolve project and normalized path for memory:// identifiers.

    Args:
        strict_project_routing: Reject a memory URL whose leading project-like
            segment cannot be resolved. Mutating tools use this to prevent a
            failed route from falling back to the active project.
        allow_missing_project_fallback: When strict routing is enabled, still
            allow a genuinely missing project prefix to be treated as an active-
            project path. This is safe only for mutations that require an existing
            target and cannot create content.
        cache_resolved_project: Persist a project resolved from the memory URL in
            MCP context. Set this to false for validation-only routing that may
            reject a resolved cross-project source.

    Returns:
        Tuple of (active_project, normalized_path, is_memory_url)

    Raises:
        UnresolvedProjectRouteError: If strict routing is enabled and the
            memory URL's leading project segment does not resolve.
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
        cached_project = await _get_cached_active_project(context)
        cached_workspace = await _get_cached_active_workspace(context)
        if cached_project and cached_workspace:
            workspace_prefix = generate_permalink(cached_workspace.slug)
            qualified_prefix = f"{workspace_prefix}/{cached_project.permalink}"
            if normalized_path == qualified_prefix or normalized_path.startswith(
                f"{qualified_prefix}/"
            ):
                remainder = (
                    ""
                    if normalized_path == qualified_prefix
                    else normalized_path.removeprefix(f"{qualified_prefix}/")
                )
                resolved_path = _canonical_memory_path_for_workspace(
                    workspace_slug=cached_workspace.slug,
                    workspace_type=cached_workspace.workspace_type,
                    project_permalink=cached_project.permalink,
                    remainder=remainder,
                )
                return cached_project, resolved_path, True

        workspace_context = current_workspace_permalink_context()
        if workspace_context and project:
            workspace_prefix = generate_permalink(workspace_context.workspace_slug)
            project_permalink = generate_permalink(_unqualified_project_identifier(project))
            qualified_prefix = f"{workspace_prefix}/{project_permalink}"
            if normalized_path == qualified_prefix or normalized_path.startswith(
                f"{qualified_prefix}/"
            ):
                active_project = await get_active_project(client, project, context, headers)
                remainder = (
                    ""
                    if normalized_path == qualified_prefix
                    else normalized_path.removeprefix(f"{qualified_prefix}/")
                )
                resolved_path = _canonical_memory_path_for_workspace(
                    workspace_slug=workspace_context.workspace_slug,
                    workspace_type=workspace_context.workspace_type,
                    project_permalink=project_permalink,
                    remainder=remainder,
                )
                return active_project, resolved_path, True

        project_prefix, remainder = _split_project_prefix(normalized_path)
        include_project = config.permalinks_include_project
        # Trigger: memory URL begins with a potential project segment
        # Why: allow project-scoped memory URLs without requiring a separate project parameter
        # Outcome: attempt to resolve the prefix as a project and route to it
        if project_prefix:
            # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
            from mcp.server.fastmcp.exceptions import ToolError

            if cached_project and _project_matches_identifier(cached_project, project_prefix):
                resolved_project = await resolve_project_parameter(project_prefix, context=context)
                if resolved_project and generate_permalink(resolved_project) != generate_permalink(
                    project_prefix
                ):
                    raise ValueError(
                        f"Project is constrained to '{resolved_project}', cannot use '{project_prefix}'."
                    )

                resolved_path = _canonical_memory_path_for_active_route(
                    cached_project,
                    remainder,
                    include_project=include_project,
                    cached_workspace=cached_workspace,
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
                error_message = str(exc).lower()
                project_route_missing = "project not found" in error_message
                project_route_hidden_by_scope = (
                    "does not have access to this project" in error_message
                    and cached_project is not None
                    and _project_matches_identifier(cached_project, project)
                    and not strict_project_routing
                )
                if not project_route_missing and not project_route_hidden_by_scope:
                    raise
                if strict_project_routing and not (
                    project_route_missing and allow_missing_project_fallback
                ):
                    # Mutations that can create content must not reinterpret a
                    # missing project route as an active-project path (#1066).
                    # Existing-target mutations may opt into that legacy path
                    # fallback, while scope-hidden routes always fail above.
                    raise UnresolvedProjectRouteError(identifier, project_prefix) from exc
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
                if cache_resolved_project:
                    await _set_cached_active_project(context, active_project)

                resolved_path = _canonical_memory_path_for_active_route(
                    active_project,
                    remainder,
                    include_project=include_project,
                    cached_workspace=cached_workspace,
                )
                return active_project, resolved_path, True

        # Trigger: memory URL has no resolvable project route segment
        # Why: preserve active-project behavior while honoring workspace paths
        # Outcome: normalize against the already-selected local/cloud route
        active_project = await get_active_project(client, project, context, headers)
        resolved_path = _canonical_memory_path_for_active_route(
            active_project,
            normalized_path,
            include_project=include_project,
            cached_workspace=cached_workspace,
        )
        return active_project, resolved_path, True


async def detect_project_from_memory_url_prefix(
    identifier: str,
    config: BasicMemoryConfig,
    context: Optional[Context] = None,
) -> Optional[str]:
    """Resolve a project from a memory URL prefix, including workspace-qualified URLs."""
    if not identifier.strip().startswith("memory://"):
        return None

    return await detect_project_from_identifier_prefix(identifier, config, context=context)


async def detect_project_from_identifier_prefix(
    identifier: str,
    config: BasicMemoryConfig,
    context: Optional[Context] = None,
) -> Optional[str]:
    """Resolve a project from a plain permalink, memory URL, or workspace route prefix."""
    local_project = detect_project_from_url_prefix(identifier, config)
    if local_project is not None:
        return local_project

    normalized_identifier = normalize_project_reference(_identifier_path(identifier)).strip("/")
    if "/" not in normalized_identifier:
        # Trigger: plain text search query or single-segment title/permalink.
        # Why: cloud project discovery can build a workspace index; only path-shaped
        #   identifiers carry enough structure to justify that cost.
        # Outcome: keep unqualified search/title input on the active/default project route.
        return None

    if _workspace_identifier_discovery_available(identifier, config):
        workspace_discovery_fallback_errors = (
            "not found",
            "no accessible workspaces",
            "unable to discover",
        )
        try:
            workspace_resolution = await resolve_workspace_qualified_identifier(
                identifier,
                context=context,
            )
        except ValueError as exc:
            message = str(exc).lower()
            if any(error in message for error in workspace_discovery_fallback_errors):
                return None
            raise

        if workspace_resolution is not None:
            return workspace_resolution.project_identifier

        project_prefix, _ = _split_project_prefix(normalized_identifier)
        if project_prefix is None:
            return None

        try:
            project_resolution = await resolve_workspace_project_identifier(
                project_prefix,
                context=context,
            )
        except ValueError as exc:
            message = str(exc).lower()
            if any(error in message for error in workspace_discovery_fallback_errors):
                return None
            raise

        return project_resolution.qualified_name

    return None


@asynccontextmanager
async def get_project_client(
    project: Optional[str] = None,
    context: Optional[Context] = None,
    project_id: Optional[str] = None,
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

    Args:
        project: Optional explicit project parameter (name or permalink)
        context: Optional FastMCP context for caching
        project_id: Optional project external_id (UUID). When provided, takes
            precedence over ``project`` and disambiguates the project across
            workspaces. Use this when the same project name exists in multiple
            cloud workspaces.

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

    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    # When project_id (UUID) is provided, prefer it as the resolution identifier.
    # external_id is unambiguous across workspaces; project name can collide.
    project_identifier = project_id if project_id else project

    # Step 1: Resolve project name from config (no network call)
    resolved_project = await resolve_project_parameter(project_identifier, context=context)
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
        await _clear_cached_active_workspace_for_local_route(context)
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

    # Trigger: identifier is a UUID (project_id) but local config keys by name only
    # Why: get_project_mode defaults to CLOUD for unknown identifiers; a UUID is
    #   never registered in local config, so it would always falsely route cloud
    # Outcome: in pure local mode, treat UUID identifiers as local routing; cloud
    #   discovery still happens when factory/explicit/credentials are present
    cloud_available = factory_mode or explicit_cloud_routing or has_cloud_credentials(config)
    if project_id and not cloud_available:
        project_mode = ProjectMode.LOCAL

    # Trigger: project_id is a local external_id in a mixed local+cloud setup.
    # Why: UUIDs are not local config keys, so get_project_mode() treats them as
    #   cloud projects. A local-first probe avoids making local UUIDs depend on
    #   healthy cloud workspace discovery.
    # Outcome: resolve the effective UUID against local ASGI first; if it is not
    #   local, preserve the existing cloud workspace lookup path.
    if (
        project_id
        and config.projects
        and not factory_mode
        and not explicit_cloud_routing
        and project_mode == ProjectMode.CLOUD
    ):
        try:
            canonical_project_id = str(UUID(resolved_project))
        except ValueError:
            pass
        else:
            with logfire.span(
                "routing.local_project_id_probe",
                project_id=canonical_project_id,
            ):
                async with get_client() as client:
                    try:
                        active_project = await get_active_project(
                            client,
                            canonical_project_id,
                            context,
                        )
                    except ToolError as exc:
                        if "not found" not in str(exc).lower():
                            raise
                    else:
                        route_mode = "local_asgi"
                        await _clear_cached_active_workspace_for_local_route(context)
                        with logfire.span(
                            "routing.client_session",
                            project_name=active_project.name,
                            route_mode=route_mode,
                        ):
                            logger.debug("Using local ASGI routing for project_id")
                            yield client, active_project
                        return

    if factory_mode or project_mode == ProjectMode.CLOUD or explicit_cloud_routing:
        route_mode = "factory" if factory_mode else "cloud_proxy"
        active_ws: WorkspaceInfo | None = None
        resolved_entry: WorkspaceProjectEntry | None = None
        workspace_id: str
        project_for_api = _unqualified_project_identifier(resolved_project)

        if project_entry and project_entry.workspace_id:
            # Per-project config stores the cloud tenant id directly
            workspace_id = project_entry.workspace_id
            active_ws = await _workspace_metadata_by_tenant_id(workspace_id, context=context)
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
        if resolved_entry is not None:
            cached_project = await _get_cached_active_project(context)
            if (
                cached_project is not None
                and cached_project.external_id != resolved_entry.project.external_id
            ):
                await _clear_cached_active_project(context)
        with logfire.span(
            "routing.client_session",
            project_name=project_for_api,
            route_mode=route_mode,
            workspace_id=workspace_id,
        ):
            logger.debug("Using resolved workspace for cloud project routing")
            permalink_context = (
                workspace_permalink_context(active_ws.slug, active_ws.workspace_type)
                if active_ws is not None
                else nullcontext()
            )
            with permalink_context:
                async with get_client(
                    project_name=project_for_api,
                    workspace=workspace_id,
                ) as client:
                    active_project = await get_active_project(client, project_for_api, context)
                    yield client, active_project
        return

    # Step 4: Local routing (default)
    route_mode = "local_asgi"
    await _clear_cached_active_workspace_for_local_route(context)
    with logfire.span(
        "routing.client_session",
        project_name=resolved_project,
        route_mode=route_mode,
    ):
        logger.debug("Using default local ASGI routing for project client")
        # Trigger: UUID identifiers won't match name-keyed local config entries.
        # Why: get_client(project_name=<uuid>) would consult get_project_mode and
        #   default to CLOUD for unknown identifiers, breaking pure-local routing.
        # Outcome: skip per-project routing for UUIDs — local mode routes every
        #   project through the same ASGI client; the API resolves the UUID below.
        client_kwargs = {} if project_id else {"project_name": resolved_project}
        async with get_client(**client_kwargs) as client:
            active_project = await get_active_project(client, resolved_project, context)
            yield client, active_project
