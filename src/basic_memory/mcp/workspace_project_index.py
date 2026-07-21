"""Workspace project index values, context cache helpers, and pure lookup logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Sequence, cast
from uuid import UUID

from basic_memory.schemas.cloud import WorkspaceInfo
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.utils import generate_permalink

if TYPE_CHECKING:
    from fastmcp import Context

WORKSPACE_PROJECT_INDEX_STATE_KEY = "workspace_project_index"


class WorkspaceProjectLookupMiss(ValueError):
    """A project was absent from the workspace index rather than ambiguous.

    Misses are retried once against a freshly rebuilt index because the session
    cache may predate an out-of-band project creation.
    """


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
    """Session-local project lookup keyed by permalink and external_id."""

    workspaces: tuple[WorkspaceInfo, ...]
    entries: tuple[WorkspaceProjectEntry, ...]
    entries_by_permalink: dict[str, tuple[WorkspaceProjectEntry, ...]]
    entries_by_external_id: dict[str, WorkspaceProjectEntry] = field(default_factory=dict)
    failed_workspaces: tuple[WorkspaceInfo, ...] = ()


async def get_cached_active_project(context: Optional[Context]) -> Optional[ProjectItem]:
    """Return the cached active project from context when available."""
    if not context:
        return None

    cached_raw = await context.get_state("active_project")
    if isinstance(cached_raw, dict):
        return ProjectItem.model_validate(cached_raw)
    return None


async def set_cached_active_project(
    context: Optional[Context],
    active_project: ProjectItem,
) -> None:
    """Persist the active project and known default-project metadata in context."""
    if not context:
        return

    await context.set_state("active_project", active_project.model_dump())
    if active_project.is_default:
        await context.set_state("default_project_name", active_project.name)


async def clear_cached_active_project(context: Optional[Context]) -> None:
    """Clear cached project metadata that may no longer match the active route."""
    if not context:
        return

    await context.set_state("active_project", None)
    await context.set_state("default_project_name", None)


async def get_cached_active_workspace(context: Optional[Context]) -> Optional[WorkspaceInfo]:
    """Return the cached active workspace from context when available."""
    if not context:
        return None

    cached_raw = await context.get_state("active_workspace")
    if isinstance(cached_raw, dict):
        return WorkspaceInfo.model_validate(cached_raw)
    return None


async def set_cached_active_workspace(
    context: Optional[Context],
    active_workspace: WorkspaceInfo,
) -> None:
    """Persist workspace context and clear project cache when the tenant changes."""
    if not context:
        return

    cached_workspace = await get_cached_active_workspace(context)
    if cached_workspace and cached_workspace.tenant_id != active_workspace.tenant_id:
        # Project names are only unique inside one workspace. A cached project
        # from the previous tenant must not survive a workspace route change.
        await clear_cached_active_project(context)

    await context.set_state("active_workspace", active_workspace.model_dump())


async def clear_cached_active_workspace_for_local_route(
    context: Optional[Context],
) -> None:
    """Drop tenant workspace metadata before routing through a local project."""
    if not context:
        return

    await context.set_state("active_workspace", None)


async def get_cached_default_project(context: Optional[Context]) -> Optional[str]:
    """Return the cached default project name from context when available."""
    if not context:
        return None

    cached_default = await context.get_state("default_project_name")
    if isinstance(cached_default, str):
        return cached_default
    return None


def workspace_project_index_from_state(raw: object) -> WorkspaceProjectIndex | None:
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
    return build_workspace_project_index(
        workspaces,
        tuple(entries_list),
        failed_workspaces=failed_workspaces,
    )


def workspace_project_index_to_state(index: WorkspaceProjectIndex) -> dict:
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


def build_workspace_project_index(
    workspaces: tuple[WorkspaceInfo, ...],
    entries: tuple[WorkspaceProjectEntry, ...],
    *,
    failed_workspaces: tuple[WorkspaceInfo, ...] = (),
) -> WorkspaceProjectIndex:
    """Build permalink and external_id lookup tables for workspace-project entries."""
    grouped: dict[str, list[WorkspaceProjectEntry]] = {}
    by_external_id: dict[str, WorkspaceProjectEntry] = {}
    for entry in entries:
        grouped.setdefault(entry.project.permalink, []).append(entry)
        by_external_id[entry.project.external_id] = entry

    return WorkspaceProjectIndex(
        workspaces=workspaces,
        entries=entries,
        entries_by_permalink={
            permalink: tuple(items)
            for permalink, items in sorted(grouped.items(), key=lambda item: item[0])
        },
        entries_by_external_id=by_external_id,
        failed_workspaces=failed_workspaces,
    )


def format_qualified_choices(entries: Sequence[WorkspaceProjectEntry]) -> str:
    """Format qualified project choices for collision errors."""
    return " or ".join(entry.qualified_name for entry in entries)


def match_workspace_identifier(
    workspaces: tuple[WorkspaceInfo, ...],
    workspace_identifier: str,
) -> WorkspaceInfo:
    """Resolve a qualified route segment by slug, tenant_id, then display name."""
    slug_matches = [
        workspace
        for workspace in workspaces
        if workspace.slug.casefold() == workspace_identifier.casefold()
    ]
    if slug_matches:
        return slug_matches[0]

    tenant_matches = [
        workspace for workspace in workspaces if workspace.tenant_id == workspace_identifier
    ]
    if tenant_matches:
        return tenant_matches[0]

    name_matches = [
        workspace
        for workspace in workspaces
        if workspace.name.casefold() == workspace_identifier.casefold()
    ]
    if len(name_matches) > 1:
        candidates = ", ".join(workspace.slug for workspace in name_matches)
        raise ValueError(
            f"Workspace name '{workspace_identifier}' matched multiple workspaces "
            f"(slugs: {candidates}). Use the workspace slug or tenant_id to disambiguate."
        )
    if name_matches:
        return name_matches[0]

    available = ", ".join(workspace.slug for workspace in workspaces)
    raise ValueError(
        f"Workspace '{workspace_identifier}' was not found by slug, tenant_id, or name. "
        f"Available workspace slugs: {available}"
    )


async def resolve_workspace_project_from_index(
    index: WorkspaceProjectIndex,
    project: str,
    context: Optional[Context] = None,
) -> WorkspaceProjectEntry:
    """Resolve a project against one concrete workspace-index snapshot."""
    try:
        canonical_external_id = str(UUID(project))
        entry = index.entries_by_external_id.get(canonical_external_id)
        if entry:
            return entry
    except ValueError:
        pass

    from basic_memory.mcp.project_context_identifiers import split_qualified_project_identifier

    workspace_identifier, project_identifier = split_qualified_project_identifier(project)
    project_permalink = generate_permalink(project_identifier)

    if workspace_identifier:
        workspace = match_workspace_identifier(index.workspaces, workspace_identifier)
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
                raise WorkspaceProjectLookupMiss(
                    f"Projects for workspace '{workspace.name}' ({workspace.slug}) "
                    "could not be loaded. Retry after workspace discovery recovers."
                )
            available = ", ".join(
                entry.qualified_name
                for entry in index.entries
                if entry.workspace.tenant_id == workspace.tenant_id
            )
            raise WorkspaceProjectLookupMiss(
                f"Project '{project_identifier}' was not found in workspace "
                f"'{workspace.name}' ({workspace.slug}). Available projects: {available}"
            )
        if len(matches) > 1:
            details = ", ".join(
                f"{entry.qualified_name} ({entry.project.external_id})" for entry in matches
            )
            raise ValueError(
                f"Project '{project_identifier}' matched multiple projects in workspace "
                f"'{workspace.name}' ({workspace.slug}). Project permalinks must be unique. "
                f"Matches: {details}"
            )
        return matches[0]

    matches = list(index.entries_by_permalink.get(project_permalink, ()))
    if not matches:
        failed_note = ""
        if index.failed_workspaces:
            failed = ", ".join(workspace.slug for workspace in index.failed_workspaces)
            failed_note = (
                f" Project discovery failed for workspace(s): {failed}; "
                "retry or use a qualified project from an indexed workspace."
            )
        available = ", ".join(entry.qualified_name for entry in index.entries)
        raise WorkspaceProjectLookupMiss(
            f"Project '{project}' was not found in indexed cloud workspaces. "
            f"Available projects: {available}.{failed_note}"
        )

    cached_workspace = await get_cached_active_workspace(context)
    if cached_workspace:
        cached_matches = [
            entry for entry in matches if entry.workspace.tenant_id == cached_workspace.tenant_id
        ]
        if cached_matches:
            return cached_matches[0]

    if len(matches) > 1:
        default_match = next((entry for entry in matches if entry.workspace.is_default), None)
        if default_match:
            return default_match

        choices = format_qualified_choices(matches)
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
