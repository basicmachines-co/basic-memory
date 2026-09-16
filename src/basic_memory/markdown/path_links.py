"""Project-local file targets: Markdown links and path-shaped wikilinks.

A path target names one file by where it sits, relative to the note that carries
it. It never resolves through a title, a permalink, a filename alias or another
project. The parser records the author's spelling; resolution turns it into a
project-root path once the note's own path is known, which is a database fact and
not a filesystem one.
"""

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

PATH_TARGET_PREFIXES = ("/", "./", "../")


def is_path_target(target: str) -> bool:
    """Whether a relation target names a project file by path rather than identity.

    Rooted (``/``) and explicitly relative (``./``, ``../``) spellings are paths in
    Markdown links and wikilinks alike.
    """
    return target.startswith(PATH_TARGET_PREFIXES)


def markdown_link_path(href: str) -> str | None:
    """Return the authored project path of a Markdown link, or None for anything else.

    URLs, ``mailto:`` and ``file:`` links, fragment-only links, and paths carrying
    backslashes or null bytes are prose, not relations. Percent-encoding is decoded
    and the query and fragment are dropped. The result keeps the author's spelling
    relative to the note; a bare relative path is marked ``./`` so the stored
    target says it is a path and not a title.
    """
    try:
        parsed = urlsplit(href)
    except ValueError:
        # A malformed URL remains authored prose, not a failed note write.
        return None
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    path = unquote(parsed.path)
    if "\\" in path or "\x00" in path or path.endswith("/"):
        # Backslashes and null bytes are not project paths; a trailing slash
        # names a folder, and only files carry relations.
        return None
    return path if is_path_target(path) else f"./{path}"


def resolve_project_path(target: str, source_path: str | None) -> str | None:
    """Resolve a path target against the note that carries it, as a project-root path.

    ``source_path`` is the note's own project path, such as ``notes/source.md``.
    Rooted targets ignore it. Relative targets resolve against its folder, or
    against the project root when no source is known. ``.`` and ``..`` segments
    collapse, and a target that climbs past the root names no project file.
    """
    if not is_path_target(target):
        return None
    parts: list[str] = []
    if not target.startswith("/") and source_path:
        parts = list(PurePosixPath(source_path).parent.parts)
    for part in target.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/" + "/".join(parts) if parts else None
