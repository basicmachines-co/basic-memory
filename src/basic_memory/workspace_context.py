"""Request-local workspace context for canonical permalink generation."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

WORKSPACE_SLUG_HEADER = "X-Basic-Memory-Workspace-Slug"
WORKSPACE_TYPE_HEADER = "X-Basic-Memory-Workspace-Type"


@dataclass(frozen=True)
class WorkspacePermalinkContext:
    """Workspace metadata needed to build canonical organization permalinks."""

    workspace_slug: str
    workspace_type: str

    @property
    def should_prefix_permalinks(self) -> bool:
        return self.workspace_type == "organization" and bool(self.workspace_slug)


_workspace_permalink_context: ContextVar[WorkspacePermalinkContext | None] = ContextVar(
    "basic_memory_workspace_permalink_context",
    default=None,
)


def current_workspace_permalink_context() -> WorkspacePermalinkContext | None:
    """Return the active workspace permalink context, when one is set."""
    return _workspace_permalink_context.get()


@contextmanager
def workspace_permalink_context(
    workspace_slug: str | None,
    workspace_type: str | None,
) -> Iterator[None]:
    """Set request-local workspace permalink metadata.

    Cloud can populate this per request without storing workspace metadata in
    local project config. The slug/type pair is all permalink generation needs.
    """
    if bool(workspace_slug) != bool(workspace_type):
        raise ValueError("workspace_slug and workspace_type must be provided together")

    if not workspace_slug or not workspace_type:
        yield
        return

    token = _workspace_permalink_context.set(
        WorkspacePermalinkContext(
            workspace_slug=workspace_slug,
            workspace_type=workspace_type,
        )
    )
    try:
        yield
    finally:
        _workspace_permalink_context.reset(token)


def workspace_permalink_headers() -> dict[str, str]:
    """Return HTTP headers for forwarding workspace permalink context."""
    context = current_workspace_permalink_context()
    if context is None:
        return {}

    return {
        WORKSPACE_SLUG_HEADER: context.workspace_slug,
        WORKSPACE_TYPE_HEADER: context.workspace_type,
    }


def workspace_slug_for_canonical_permalinks() -> str | None:
    """Return the workspace slug when new permalinks should include it."""
    context = current_workspace_permalink_context()
    if context and context.should_prefix_permalinks:
        return context.workspace_slug
    return None
