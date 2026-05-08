"""Request-local workspace context for canonical permalink generation."""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

WORKSPACE_SLUG_HEADER = "X-Basic-Memory-Workspace-Slug"
WORKSPACE_TYPE_HEADER = "X-Basic-Memory-Workspace-Type"
_WORKSPACE_SLUG_PATTERN = re.compile(r"^[a-z0-9_-]+$")
_WORKSPACE_TYPES = {"personal", "organization"}


@dataclass(frozen=True)
class WorkspacePermalinkContext:
    """Workspace metadata needed to build canonical workspace permalinks."""

    workspace_slug: str
    workspace_type: str

    @property
    def should_prefix_permalinks(self) -> bool:
        return bool(self.workspace_slug)


_workspace_permalink_context: ContextVar[WorkspacePermalinkContext | None] = ContextVar(
    "basic_memory_workspace_permalink_context",
    default=None,
)


def current_workspace_permalink_context() -> WorkspacePermalinkContext | None:
    """Return the active workspace permalink context, when one is set."""
    return _workspace_permalink_context.get()


def validate_workspace_permalink_context_values(
    workspace_slug: str | None,
    workspace_type: str | None,
) -> None:
    """Validate workspace permalink metadata before it can affect stored permalinks."""
    validation_error = workspace_permalink_context_validation_error(workspace_slug, workspace_type)
    if validation_error is not None:
        raise ValueError(validation_error)


def workspace_permalink_context_validation_error(
    workspace_slug: str | None,
    workspace_type: str | None,
) -> str | None:
    """Return the validation error for workspace permalink metadata, if any."""
    if bool(workspace_slug) != bool(workspace_type):
        return "workspace_slug and workspace_type must be provided together"

    if not workspace_slug or not workspace_type:
        return None

    if _WORKSPACE_SLUG_PATTERN.fullmatch(workspace_slug) is None:
        return f"{WORKSPACE_SLUG_HEADER} must match [a-z0-9_-]+"

    if workspace_type not in _WORKSPACE_TYPES:
        allowed = ", ".join(sorted(_WORKSPACE_TYPES))
        return f"{WORKSPACE_TYPE_HEADER} must be one of: {allowed}"

    return None


@contextmanager
def workspace_permalink_context(
    workspace_slug: str | None,
    workspace_type: str | None,
) -> Iterator[None]:
    """Set request-local workspace permalink metadata.

    Cloud can populate this per request without storing workspace metadata in
    local project config. The slug/type pair is all permalink generation needs.
    """
    validate_workspace_permalink_context_values(workspace_slug, workspace_type)

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
