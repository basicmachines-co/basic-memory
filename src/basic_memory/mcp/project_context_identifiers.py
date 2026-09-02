"""Pure project/workspace identifier parsing and canonical path construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from basic_memory.config import BasicMemoryConfig
from basic_memory.mcp.workspace_project_index import WorkspaceProjectEntry
from basic_memory.schemas.cloud import WorkspaceInfo
from basic_memory.schemas.memory import memory_url_path
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.utils import (
    build_qualified_permalink_reference,
    generate_permalink,
    normalize_project_reference,
)
from basic_memory.workspace_context import current_workspace_permalink_context


class UnresolvedProjectRouteError(ValueError):
    """A mutating memory URL named a project prefix that could not be resolved."""

    def __init__(self, identifier: str, project_prefix: str):
        self.identifier = identifier
        self.project_prefix = project_prefix
        super().__init__(
            f"Memory URL project route '{project_prefix}' could not be resolved; "
            "refusing to treat the URL as a path in the active project."
        )


@dataclass(frozen=True)
class WorkspaceMemoryUrlResolution:
    """Resolved workspace/project route for a workspace-qualified memory URL."""

    entry: WorkspaceProjectEntry
    canonical_path: str

    @property
    def project_identifier(self) -> str:
        return self.entry.qualified_name


def canonicalize_project_name(
    project_name: Optional[str],
    config: BasicMemoryConfig,
) -> Optional[str]:
    """Return the configured name when an identifier matches by permalink."""
    if project_name is None:
        return None

    # Exact before fuzzy, the same ordering the v2 project router and the
    # workspace index use. It matters when two configured names share a
    # permalink: matching by permalink alone would answer a caller who named one
    # of them exactly with whichever the config happened to list first — the
    # other project — which is precisely the escape hatch the ambiguity error
    # tells them to use.
    if project_name in config.projects:
        return project_name

    requested_permalink = generate_permalink(project_name)
    for configured_name in config.projects:
        if generate_permalink(configured_name) == requested_permalink:
            return configured_name
    return project_name


def project_matches_identifier(project_item: ProjectItem, identifier: Optional[str]) -> bool:
    """Return True when the identifier refers to the cached project."""
    if identifier is None:
        return True
    normalized_identifier = generate_permalink(identifier)
    return normalized_identifier in {
        generate_permalink(project_item.name),
        project_item.permalink,
    }


def split_qualified_project_identifier(identifier: str) -> tuple[str | None, str]:
    """Split ``<workspace-slug>/<project>`` identifiers for cloud routing.

    This guesses: a project name may contain '/', so 'acme/docs' and
    'Research/2026' are the same shape and no string can say which is which.
    Call it only after an exact whole-identifier lookup has already missed, so
    the guess is a fallback rather than the first reading — see
    ``resolve_workspace_project_from_index`` and the v2 project router, which
    both resolve exact-first for that reason. To compare two spellings of the
    same project, use ``_workspace_qualifies`` instead, which needs no guess.
    """
    cleaned = identifier.strip()
    if "/" not in cleaned:
        return None, cleaned
    workspace_slug, project_identifier = cleaned.split("/", 1)
    if not workspace_slug or not project_identifier:
        return None, cleaned
    return workspace_slug, project_identifier


def identifier_path(identifier: str) -> str:
    """Return the routable path portion of a raw identifier or memory URL."""
    stripped = identifier.strip()
    return memory_url_path(stripped) if stripped.startswith("memory://") else stripped


def split_project_permalink_prefix(
    path: str,
    permalinks: Iterable[str],
) -> tuple[str, str] | None:
    """Split a path into the project permalink it names and the remainder.

    This is the *only* way to turn a path-shaped input into a project plus a
    project-relative path, and it deliberately takes the candidate set as an
    argument: a project name may contain '/', and ``generate_permalink``
    preserves it, so how many leading segments a project consumes is a fact
    about the known projects, never about the string. Callers that split on
    the first '/' instead kept rediscovering that — for mounts (#1421), then
    again for workspace routes.

    The longest permalink wins, which is also the only reading that can be
    right when one project's permalink prefixes another's ('research' beside
    'research/2026'). Returns None when no permalink claims the leading
    segments; the remainder has no leading slash and is "" at the root.
    """
    segments = normalize_project_reference(identifier_path(path)).strip("/").split("/")
    # An empty interior segment ('a//b') names nothing. Matching around it would
    # repair a malformed path into a route the caller did not write.
    if not all(segments):
        return None

    claimed: tuple[str, str] | None = None
    claimed_depth = 0
    for permalink in permalinks:
        depth = permalink.count("/") + 1
        if depth > len(segments) or depth <= claimed_depth:
            continue
        if generate_permalink("/".join(segments[:depth])) != permalink:
            continue
        claimed = (permalink, "/".join(segments[depth:]))
        claimed_depth = depth
    return claimed


def split_workspace_slug_prefix(identifier: str) -> tuple[str, str] | None:
    """Split ``<workspace-slug>/<rest>`` — the workspace half of a qualified route.

    A workspace slug is always one segment, so this parse is pure shape. What
    follows it is a project permalink plus a path, which only
    ``split_project_permalink_prefix`` can separate.
    """
    normalized = normalize_project_reference(identifier_path(identifier)).strip("/")
    workspace_slug, _, rest = normalized.partition("/")
    # A rest starting with '/' means the project segment is empty ('acme//notes').
    # The caller wrote nothing there, so this is not a workspace route.
    if not workspace_slug or not rest or rest.startswith("/"):
        return None
    return workspace_slug, rest


def is_workspace_route_shaped(identifier: str) -> bool:
    """True when an identifier has enough segments to spell workspace/project/path.

    A shape question only — it gates whether workspace discovery may be
    consulted at all, and deliberately says nothing about where the project
    ends. Three segments is the unmistakable form: fewer could equally be a
    project-relative path in the session's own project.
    """
    normalized = normalize_project_reference(identifier_path(identifier)).strip("/")
    parts = normalized.split("/", 2)
    return len(parts) == 3 and all(parts)


def canonical_memory_path_for_workspace(
    *,
    workspace_slug: str,
    workspace_type: str,
    project_permalink: str,
    remainder: str,
) -> str:
    """Return the stored canonical path for a workspace-qualified memory URL."""
    normalized_remainder = remainder.strip("/")
    if workspace_type not in {"organization", "personal"}:
        raise ValueError(f"Unsupported workspace_type for memory URL routing: {workspace_type}")
    if not normalized_remainder:
        normalized_remainder = project_permalink
    if "*" in normalized_remainder and current_workspace_permalink_context() is None:
        return build_qualified_permalink_reference(
            project_permalink,
            normalized_remainder,
            include_project=True,
        )
    return build_qualified_permalink_reference(
        project_permalink,
        normalized_remainder,
        include_project=True,
        workspace_permalink=workspace_slug,
    )


def canonical_memory_path_for_active_route(
    active_project: ProjectItem,
    path: str,
    *,
    include_project: bool,
    cached_workspace: WorkspaceInfo | None = None,
) -> str:
    """Return the canonical permalink path for the active project/workspace."""
    project_prefix = active_project.permalink
    if "*" in path and current_workspace_permalink_context() is None:
        if not include_project:
            return path
        if path == project_prefix or path.startswith(f"{project_prefix}/"):
            return path
        return f"{project_prefix}/{path}"

    workspace_remainder = path
    if include_project and (path == project_prefix or path.startswith(f"{project_prefix}/")):
        workspace_remainder = (
            "" if path == project_prefix else path.removeprefix(f"{project_prefix}/")
        )

    workspace_context = current_workspace_permalink_context()
    if workspace_context is not None:
        return canonical_memory_path_for_workspace(
            workspace_slug=workspace_context.workspace_slug,
            workspace_type=workspace_context.workspace_type,
            project_permalink=active_project.permalink,
            remainder=workspace_remainder,
        )
    if cached_workspace is not None:
        return canonical_memory_path_for_workspace(
            workspace_slug=cached_workspace.slug,
            workspace_type=cached_workspace.workspace_type,
            project_permalink=active_project.permalink,
            remainder=workspace_remainder,
        )
    if not include_project:
        return path
    if path == project_prefix or path.startswith(f"{project_prefix}/"):
        return path
    return f"{project_prefix}/{path}"


def split_project_prefix(path: str) -> tuple[Optional[str], str]:
    """Split a possible project prefix from a memory URL path."""
    if "/" not in path:
        return None, path
    project_prefix, remainder = path.split("/", 1)
    if not project_prefix or not remainder or "*" in project_prefix:
        return None, path
    return project_prefix, remainder


def add_project_metadata(result: str, project_name: str) -> str:
    """Add project context as a metadata footer for session tracking."""
    return f"{result}\n\n[Session: Using project '{project_name}']"


def detect_project_from_url_prefix(
    identifier: str,
    config: BasicMemoryConfig,
) -> Optional[str]:
    """Return the local config project matching a memory URL path prefix."""
    path = memory_url_path(identifier) if identifier.strip().startswith("memory://") else identifier
    normalized = normalize_project_reference(path)
    # The '*' guard belongs to the glob case: a globbed first segment is search
    # input, not a project prefix, and must not be matched against any project.
    prefix, _ = split_project_prefix(normalized)
    if prefix is None:
        return None

    by_permalink = {generate_permalink(name): name for name in config.projects}
    claimed = split_project_permalink_prefix(normalized, by_permalink)
    # A prefix with no remainder names the project itself, not a path in it;
    # split_project_prefix already rejected that shape above.
    if claimed is None or not claimed[1]:
        return None
    return by_permalink[claimed[0]]
