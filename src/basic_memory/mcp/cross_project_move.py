"""Moving notes into another project (#1842).

A move across projects is a write in the target project followed by a delete in
the source project. The moved note is a new note there: it gets a new id and its
own version history, and links in either project are not rewritten.

The source is deleted only after the copy is written, and only while it is still
the revision that was copied. If that delete is refused (the note changed, or the
caller cannot delete in the source project), the copy is removed again, so a
refused move leaves both projects as they were.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, assert_never

from fastmcp.exceptions import ToolError

from basic_memory.markdown.note_lock import LOCKED_NOTE_MESSAGE, note_is_locked
from basic_memory.mcp.clients import DirectoryClient, KnowledgeClient
from basic_memory.mcp.note_targets import ProjectTarget, resolve_note
from basic_memory.mcp.refusals import (
    api_refusal,
    note_change_failure,
    refusal,
    revision_conflict,
)
from basic_memory.mcp.tool_params import OutputFormat
from basic_memory.runtime.storage import runtime_file_path_is_markdown_note
from basic_memory.schemas.base import Entity
from basic_memory.schemas.directory import DirectoryNode
from basic_memory.schemas.memory import memory_url_path
from basic_memory.schemas.v2.entity import EntityResponseV2
from basic_memory.schemas.v2.note_write import (
    NoteAlreadyExists,
    NoteCreated,
    NoteLocked,
    NoteRevisionConflict,
    NoteTargetMoved,
    NoteUpdated,
)

UNCHANGED = "Nothing was moved."
LISTING_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class MovedNote:
    """One note that now lives in the target project."""

    previous_file_path: str
    file_path: str


# --- One note ---


async def move_note_across(
    source: ProjectTarget,
    target: ProjectTarget,
    identifier: str,
    destination: str,
    expected_checksum: str | None,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    """Move one note into the target project; refuse with both projects unchanged."""
    note = await resolve_note(
        source,
        identifier,
        output_format=output_format,
        expected_checksum=expected_checksum,
        unchanged=UNCHANGED,
    )
    # Trigger: the destination ends with '/', or is the project root.
    # Outcome: the note keeps its file name under that folder, as within a project.
    if destination.endswith("/") or not destination:
        destination_path = str(
            PurePosixPath(destination.rstrip("/")) / PurePosixPath(note.file_path).name
        )
    else:
        destination_path = destination
    moved = await _move_one(
        source,
        target,
        note.external_id,
        note.file_path,
        destination_path,
        expected_checksum,
        output_format,
    )
    result = {
        "action": "moved",
        "previous_project": source.project.name,
        "previous_file_path": source.paths.path(moved.previous_file_path),
        "project": target.project.name,
        "file_path": target.paths.path(moved.file_path),
    }
    if output_format == "json":
        return result
    return (
        f"Moved {result['previous_file_path']} (project {result['previous_project']}) to "
        f"{result['file_path']} (project {result['project']})"
    )


async def _move_one(
    source: ProjectTarget,
    target: ProjectTarget,
    external_id: str,
    file_path: str,
    destination_path: str,
    expected_checksum: str | None,
    output_format: OutputFormat,
) -> MovedNote:
    """Copy one note into the target project, then delete it from the source."""
    address = source.paths.path(file_path)
    if not runtime_file_path_is_markdown_note(file_path):
        raise refusal(
            "UNSUPPORTED",
            f"'{address}' is not a Markdown note; only notes move between projects.",
            output_format=output_format,
        )
    if not runtime_file_path_is_markdown_note(destination_path):
        raise refusal(
            "INVALID_ARGUMENTS",
            f"'{target.paths.path(destination_path)}' is not a Markdown note path.",
            try_instead="a destination ending in '.md', or a folder ending in '/'",
            output_format=output_format,
        )

    # --- Read the revision being moved ---
    # The checksum is taken before the content, so a change landing in between
    # makes the guarded delete below refuse instead of dropping that change.
    source_client = KnowledgeClient(source.client, source.project.external_id)
    entity = await source_client.get_entity(external_id)
    if expected_checksum is not None and entity.db_checksum != expected_checksum:
        raise revision_conflict(
            address,
            entity.db_checksum,
            unchanged=UNCHANGED,
            output_format=output_format,
            project=source.project_ref,
        )
    revision = entity.db_checksum
    content = (await source_client.accepted_note_contents([external_id]))[external_id]
    # Trigger: the note carries `locked: true`.
    # Why: the source refuses the delete, and the copy carries the lock too, so it
    #      could not be removed again; the note would end up in both projects.
    # Outcome: refused before anything is written, as a move within a project is.
    if note_is_locked(content):
        raise refusal("LOCKED", LOCKED_NOTE_MESSAGE, output_format=output_format)

    # --- Write the copy ---
    copy = await _write_copy(target, destination_path, content, output_format)

    # --- Delete the source ---
    try:
        await source_client.delete_entity(external_id, base_checksum=revision)
    except ToolError as error:
        copy_address = target.paths.path(copy.file_path)
        # Trigger: no answer said whether the source delete landed.
        # Why: the source may already be gone; removing the copy could lose the note.
        # Outcome: the copy stays, and the caller is told to check the source.
        if api_refusal(error) is None:
            raise refusal(
                "OUTCOME_UNKNOWN",
                f"'{address}' was copied to '{copy_address}' in project "
                f"'{target.project.name}', and its delete from project "
                f"'{source.project.name}' may or may not have happened: {error}",
                try_instead=(
                    f"bm_read(identifier={address!r}, project={source.project_ref!r}), and "
                    "bm_delete it if it is still there"
                ),
                output_format=output_format,
            ) from error
        # Trigger: the source refused the delete (the note changed since it was
        #   read, or the caller may not delete in the source project).
        # Why: the move must not leave the note in both projects.
        # Outcome: the copy is removed while it is still unchanged, and the move refuses.
        await _remove_copy(target, copy, copy_address, output_format)
        raise note_change_failure(
            error,
            action="move",
            address=address,
            unchanged=UNCHANGED,
            output_format=output_format,
            project=source.project.external_id,
        ) from error
    return MovedNote(previous_file_path=file_path, file_path=copy.file_path)


async def _write_copy(
    target: ProjectTarget, destination_path: str, content: str, output_format: OutputFormat
) -> EntityResponseV2:
    """Create the note in the target project; never replace a note already there."""
    path = PurePosixPath(destination_path)
    directory = "" if str(path.parent) == "." else str(path.parent)
    note = Entity(
        title=path.stem,
        directory=directory,
        file_name=path.name,
        content_type="text/markdown",
        content=content,
    )
    shown = target.paths.path(destination_path)
    try:
        outcome = await KnowledgeClient(target.client, target.project.external_id).write_note(
            note, overwrite=False
        )
    except ToolError as error:
        raise note_change_failure(
            error,
            action="move",
            address=shown,
            unchanged=UNCHANGED,
            output_format=output_format,
            project=target.project.external_id,
        ) from error
    match outcome:
        case NoteCreated(entity=written):
            return written
        case NoteAlreadyExists() | NoteTargetMoved():
            raise refusal(
                "ALREADY_EXISTS",
                f"A note already exists at '{shown}' in project '{target.project.name}'. "
                f"{UNCHANGED}",
                try_instead="a different destination",
                output_format=output_format,
            )
        case NoteUpdated() | NoteRevisionConflict() | NoteLocked():  # pragma: no cover
            # overwrite=False without expected_checksum creates a note or finds the
            # path taken; there is no predecessor to update, conflict with or lock.
            raise AssertionError(f"unexpected write outcome for a new note: {outcome!r}")
        case _:  # pragma: no cover
            assert_never(outcome)


async def _remove_copy(
    target: ProjectTarget, copy: EntityResponseV2, copy_address: str, output_format: OutputFormat
) -> None:
    """Delete the copy just written, unless someone has changed it since."""
    try:
        await KnowledgeClient(target.client, target.project.external_id).delete_entity(
            copy.external_id, base_checksum=copy.db_checksum
        )
    except ToolError as error:
        raise refusal(
            "PARTIAL_FAILURE",
            f"The source note could not be deleted, and its copy at '{copy_address}' in "
            f"project '{target.project.name}' could not be removed either: {error}. The note "
            "is now in both projects.",
            try_instead=f"bm_delete(identifier={copy_address!r}, project={target.project_ref!r})",
            output_format=output_format,
        ) from error


# --- A directory ---


async def move_directory_across(
    source: ProjectTarget,
    target: ProjectTarget,
    destination: str,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    """Move every note under a directory into the target project, one note at a time."""
    directory = memory_url_path(source.route.path).strip("/")
    if not directory:
        raise refusal(
            "INVALID_ARGUMENTS",
            "The project root cannot be moved.",
            output_format=output_format,
        )
    # A trailing '/' moves the directory into that folder, as it does within a project.
    if destination.endswith("/"):
        destination = f"{destination}{PurePosixPath(directory).name}"
    destination = destination.strip("/")

    files = await _files_under(source, directory)
    if not files:
        raise refusal(
            "NOT_FOUND",
            f"No files under '{source.paths.path(directory)}' in project '{source.project.name}'.",
            output_format=output_format,
        )

    moved: list[MovedNote] = []
    failed: list[dict[str, str]] = []
    for node in files:
        assert node.file_path is not None and node.external_id is not None
        relative = PurePosixPath(node.file_path).relative_to(directory)
        destination_path = (
            str(PurePosixPath(destination) / relative) if destination else str(relative)
        )
        try:
            moved.append(
                await _move_one(
                    source,
                    target,
                    node.external_id,
                    node.file_path,
                    destination_path,
                    None,
                    output_format,
                )
            )
        except ToolError as error:
            failed.append({"path": source.paths.path(node.file_path), "error": str(error)})

    result: dict[str, Any] = {
        "action": "moved",
        "previous_project": source.project.name,
        "path": source.paths.path(directory),
        "project": target.project.name,
        "destination": target.paths.path(destination),
        "total_files": len(files),
        "moved_files": [target.paths.path(note.file_path) for note in moved],
        "failed": failed,
    }
    # Trigger: some notes moved and some did not (a file that is not a note, a name
    #   already taken in the target project, a note that changed meanwhile).
    # Why: each note moved or stayed whole, but the directory did neither.
    # Outcome: an error listing what stayed, so the caller can decide what to retry.
    if failed:
        raise refusal(
            "PARTIAL_FAILURE",
            f"Moved {len(moved)} of {len(files)} files from '{result['path']}' "
            f"(project {source.project.name}) to '{result['destination']}' "
            f"(project {target.project.name}); {len(failed)} stayed: "
            + "; ".join(f"{failure['path']}: {failure['error']}" for failure in failed),
            try_instead=(
                f"bm_list(path={result['path']!r}, project={source.project_ref!r}) "
                "to see what stayed"
            ),
            output_format=output_format,
        )
    if output_format == "json":
        return result
    return (
        f"Moved directory {result['path']} (project {source.project.name}) to "
        f"{result['destination']} (project {target.project.name}) ({len(files)} files)"
    )


async def _files_under(
    source: ProjectTarget, directory: str, page_size: int = LISTING_PAGE_SIZE
) -> list[DirectoryNode]:
    """Every file under `directory`, at any depth, in listing order."""
    client = DirectoryClient(source.client, source.project.external_id)
    files: list[DirectoryNode] = []
    pending = [f"/{directory}"]
    while pending:
        folder = pending.pop(0)
        page = 1
        while True:
            listing = await client.list(folder, depth=1, page=page, page_size=page_size)
            for node in listing.nodes:
                if node.type == "directory":
                    pending.append(node.directory_path)
                else:
                    files.append(node)
            if not listing.has_more:
                break
            page += 1
    return files
