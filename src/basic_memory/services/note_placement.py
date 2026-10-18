"""Which project-relative paths a note identifier names, and where a missing one goes.

A note is addressed by a path inside its project, but callers also spell that path
with the project's own routing prefix: 'demo/scratch/note', 'team/demo/scratch/note'
on a workspace route, or 'memory://demo/scratch/note'. Resolving an existing note and
creating a missing one have to read those spellings the same way, so both use
``note_path_readings`` here (#1775).

A plain path that starts with the prefix is genuinely ambiguous: 'demo/scratch/note'
is also an ordinary path inside a folder named 'demo'. Creating a note has to pick
one, and ``place_missing_note`` decides from the folders that canonical note state
establishes: accepted notes, which are not on storage yet, and files on storage, which
are not indexed yet. The index alone can lag either.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import own_project_remainder

type NotePathReadings = tuple[str] | tuple[str, str]


def note_path_readings(
    identifier: str,
    *,
    project_permalink: str | None,
    workspace_permalink: str | None = None,
) -> NotePathReadings:
    """Every project-relative path an identifier can name, the literal path first.

    A memory URL's leading segments route, so it names one path: the one below this
    project's prefix. A plain path that starts with the prefix names two: the path as
    written, and the path below the prefix. Prefixes match by permalink, as routing
    does, so 'Demo/...' and 'My Project/...' are recognized, and the workspace-qualified
    form is too when a workspace is given.
    """
    stripped = identifier.strip()
    is_memory_url = stripped.startswith("memory://")
    path = memory_url_path(stripped) if is_memory_url else stripped
    below_prefix = own_project_remainder(
        path, project_permalink, workspace_permalink=workspace_permalink
    )
    if below_prefix is None:
        return (path,)
    if is_memory_url:
        return (below_prefix,)
    return (path, below_prefix)


def split_note_path(path: str) -> tuple[str, str]:
    """Split a project-relative note path into (directory, title); '' is the root."""
    directory, _, title = path.rpartition("/")
    return directory, title


@dataclass(frozen=True, slots=True)
class NoteCreatePlacement:
    """Create the missing note here."""

    directory: str
    title: str


@dataclass(frozen=True, slots=True)
class AmbiguousNotePlacement:
    """Both readings name folders that already hold notes, so nothing says which."""

    literal: str
    below_prefix: str


@dataclass(frozen=True, slots=True)
class OutsideProjectPlacement:
    """A reading escapes the project, so no folder was probed and nothing is created."""

    path: str


type NotePlacement = NoteCreatePlacement | AmbiguousNotePlacement | OutsideProjectPlacement


def _create_at(path: str) -> NoteCreatePlacement:
    directory, title = split_note_path(path)
    return NoteCreatePlacement(directory=directory, title=title)


async def place_missing_note(
    readings: NotePathReadings,
    *,
    within_project: Callable[[str], bool],
    folder_exists: Callable[[str], Awaitable[bool]],
    prefix_is_routing: bool,
) -> NotePlacement:
    """Decide where a note that exists under none of its readings is created.

    Every reading must stay inside the project before any folder is probed: an
    identifier like 'demo/../../other/secrets' or 'demo//tmp/x' would otherwise turn
    the folder check into an existence probe of directories the caller cannot reach.

    Folders that already hold notes decide first, accepted or on storage. The project root is no evidence,
    since it always exists. Without evidence, the permalink convention decides: when
    permalinks carry the project prefix, a path that starts with it reads as routing,
    otherwise as a folder named like the project.
    """
    escaping = next((reading for reading in readings if not within_project(reading)), None)
    if escaping is not None:
        return OutsideProjectPlacement(path=escaping)

    match readings:
        case (only,):
            return _create_at(only)
        case (literal, below_prefix):
            literal_folder, _ = split_note_path(literal)
            below_folder, _ = split_note_path(below_prefix)
            literal_exists = bool(literal_folder) and await folder_exists(literal_folder)
            below_exists = bool(below_folder) and await folder_exists(below_folder)
            match literal_exists, below_exists:
                case True, True:
                    return AmbiguousNotePlacement(literal=literal, below_prefix=below_prefix)
                case True, False:
                    return _create_at(literal)
                case False, True:
                    return _create_at(below_prefix)
                case False, False:
                    return _create_at(below_prefix if prefix_is_routing else literal)
