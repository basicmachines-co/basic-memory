"""Basic Memory project-reference routing shared by hook workflows."""

from __future__ import annotations

import re

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def split_project_ref(ref: str) -> tuple[str | None, str | None]:
    """Split a project reference into the ``(project, project_id)`` routing pair."""
    if UUID_RE.match(ref):
        return None, ref
    return ref, None
