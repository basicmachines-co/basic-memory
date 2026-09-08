"""Project-local targets carried by ordinary Markdown links."""

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit


def markdown_link_target(href: str, source_path: str) -> str | None:
    """Return a project-root path, excluding URLs and paths escaping the project."""
    try:
        parsed = urlsplit(href)
    except ValueError:
        # A malformed URL remains authored prose, not a failed note write.
        return None
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    path = unquote(parsed.path)
    if "\\" in path or "\x00" in path:
        return None
    parts = [] if path.startswith("/") else list(PurePosixPath(source_path).parent.parts)
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/" + "/".join(parts) if parts else None
