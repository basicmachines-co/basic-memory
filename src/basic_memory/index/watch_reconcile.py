"""Find files on disk that the local index does not know.

The watcher is the only thing that indexes a file written while the server runs.
Anything that makes it miss an event leaves that file on disk and out of search,
the graph and every tool, with nothing reported. The project scan at startup is
what used to repair that, which is why a restart always seemed to fix it.

Two things here. `expand_new_directories` turns a reported new directory into the
files inside it, because on Linux those files may never be reported themselves.
`settled_project_files` and `indexed_file_paths` are the two halves of the
reconcile: what is on disk, and what the index holds. The walks and stat calls
are blocking filesystem work, so both walk functions are meant to run in a worker
thread, never on the event loop.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from watchfiles import Change
from watchfiles.main import FileChange
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.index.filesystem import local_relative_path_is_filtered
from basic_memory.models import Entity

#: A file's stat signature: (mtime_ns, size). Used to recognize a file that a
#: reconcile already tried to index and that has not changed since.
FileSignature = tuple[int, int]


def expand_new_directories(
    changes: set[FileChange],
    *,
    project_root: Path,
    ignore_patterns: set[str],
) -> tuple[set[FileChange], tuple[Path, ...]]:
    """Add every file inside a newly created directory to the batch as added.

    WHY. On Linux the watcher (watchfiles, over notify's inotify backend) learns
    of a new directory from its parent's watch and then adds a watch of its own
    for it. If that directory cannot be read at that instant, the add fails and
    notify discards the error: nothing is reported, and no file written into the
    directory afterwards produces an event. `install -d -o 1000 -g 1000` run as
    root does exactly this -- the directory exists, owned by root and closed, for
    a moment before it is handed over -- so does a restore or an SSH copy made as
    root and chowned after. A bulk copy into a running server then leaves whole
    folders unindexed, and which folders depends on scheduling.

    The directory's own creation is still reported, because the parent's watch
    is fine. So treat that report as "everything in here is new": walk it and
    add each eligible file. Files written into it after this batch are the
    reconcile's job, and the restart it prompts gets the directory watched.

    Returns the expanded batch and the new directories found (top-level ones as
    reported; their subdirectories are walked, not listed).
    """
    root = project_root.expanduser().resolve()
    expanded = set(changes)
    new_directories: list[Path] = []
    for change, path in changes:
        if change != Change.added:
            continue
        directory = Path(path)
        try:
            if directory.is_symlink() or not directory.is_dir():
                continue
            relative = directory.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if local_relative_path_is_filtered(relative) or should_ignore_path(
            directory, root, ignore_patterns
        ):
            continue
        new_directories.append(directory)
        for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = [
                name
                for name in dirnames
                if not name.startswith(".")
                and not should_ignore_path(current / name, root, ignore_patterns)
            ]
            for name in filenames:
                file_path = current / name
                try:
                    if file_path.is_symlink() or not file_path.is_file():
                        continue
                    relative_file = file_path.resolve().relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                if local_relative_path_is_filtered(relative_file) or should_ignore_path(
                    file_path, root, ignore_patterns
                ):
                    continue
                expanded.add((Change.added, str(file_path)))
    return expanded, tuple(new_directories)


def expand_deleted_directories(
    changes: set[FileChange],
    *,
    project_root: Path,
    indexed_paths: set[str],
) -> set[FileChange]:
    """Add a delete for every indexed file under a directory the batch reports gone.

    The counterpart of `expand_new_directories`, and what keeps it safe. A
    directory renamed or moved inside a project arrives as one deleted directory
    and one added directory, with no events for the files in either. Expanding
    only the added side would index every file a second time under its new path
    while the old rows stayed. With both sides expanded, the move processor pairs
    each old path with its new one by checksum and moves the rows, as it does for
    a single moved file. A path that still exists is left alone: the delete
    planner only acts on confirmed absence anyway.
    """
    root = project_root.expanduser().resolve()
    expanded = set(changes)
    for change, path in changes:
        if change != Change.deleted:
            continue
        candidate = Path(path)
        if candidate.exists():
            continue
        try:
            prefix = candidate.resolve().relative_to(root).as_posix().rstrip("/") + "/"
        except ValueError:
            continue
        if prefix == "./":
            continue
        for indexed_path in indexed_paths:
            if indexed_path.startswith(prefix):
                expanded.add((Change.deleted, str(root / indexed_path)))
    return expanded


def settled_project_files(
    project_root: Path,
    *,
    ignore_patterns: set[str] | None = None,
    settle_seconds: float,
    now: float | None = None,
) -> dict[str, FileSignature]:
    """Return project-relative files that have been still for `settle_seconds`.

    Eligibility is the startup scan's (`scan_local_project_index_files`): the
    same ignore rules, hidden paths and symlink handling, so the reconcile never
    indexes a file the startup scan would leave alone.

    "Still" is judged on the later of mtime and ctime. A copy that preserves
    timestamps (`cp -p`, `tar`, `rsync -a`, a backup restore) gives a file an old
    mtime the moment it lands, but its ctime is the time of the copy, so a file
    still waiting in the watcher's own debounce window is not taken for a missed
    one.
    """
    # Imported here: local_project pulls in the services package, which imports
    # the watcher lazily; a module-level import would close that loop.
    from basic_memory.index.local_project import scan_local_project_index_files

    scan = scan_local_project_index_files(
        project_root,
        ignore_patterns=(
            ignore_patterns
            if ignore_patterns is not None
            else load_gitignore_patterns(project_root)
        ),
    )
    cutoff_ns = int(((time.time() if now is None else now) - settle_seconds) * 1_000_000_000)
    root = project_root.expanduser().resolve()
    settled: dict[str, FileSignature] = {}
    for relative_path in scan.file_paths:
        try:
            stat_result = os.stat(root / relative_path)
        except OSError:
            # Gone or unreadable between the walk and the stat: not this pass's business.
            continue
        if max(stat_result.st_mtime_ns, stat_result.st_ctime_ns) > cutoff_ns:
            continue
        settled[relative_path] = (stat_result.st_mtime_ns, stat_result.st_size)
    return settled


async def indexed_file_paths(
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
) -> set[str]:
    """Return every file path the index holds for one project (one cheap query)."""
    query = select(Entity.file_path).where(Entity.project_id == project_id)
    async with db.scoped_session(session_maker) as session:
        rows = (await session.execute(query)).scalars().all()
    return {str(file_path) for file_path in rows}
