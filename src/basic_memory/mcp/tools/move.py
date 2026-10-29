"""bm_move: move or rename a note or a directory within a project.

`identifier` names a note and `path` a directory. A trailing '/' on `destination`
moves into that folder under the current name. Moves stay inside one project; a
destination whose first folder names another project is refused.
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, Field

from basic_memory.mcp.async_client import local_project_path
from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.move_destinations import project_named_by_destination
from basic_memory.mcp.note_targets import (
    ProjectTarget,
    open_project_target,
    resolve_note,
)
from basic_memory.mcp.note_writes import NoteChange
from basic_memory.mcp.refusals import note_change_failure, refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import OutputFormat, ProjectParam
from basic_memory.runtime.storage import RUNTIME_MARKDOWN_FILE_SUFFIXES
from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import generate_permalink, validate_project_path


@mcp.tool(
    name="bm_move",
    title="Move note",
    description=(
        "Move or rename a Basic Memory note (`identifier`) or a directory (`path`) within "
        "its project. `destination` is the new project-relative path; ending it with '/' "
        "moves into that folder under the current name ('archive/'). A note keeps "
        "its file extension ('.markdown' may become '.md'). Pass `expected_checksum` (from "
        "bm_read) to move a note only if it is still the revision you read. Moving into "
        "another project is refused: bm_read, bm_write there, then bm_delete."
    ),
    tags={"notes"},
    annotations={
        "title": "Move note",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_move(
    destination: Annotated[
        str,
        Field(
            min_length=1,
            validation_alias=AliasChoices(
                "destination", "destination_path", "destination_folder", "to", "new_path"
            ),
            description=(
                "New path, e.g. 'archive/plan.md'. A trailing '/' ('archive/') moves the note "
                "or directory into that folder under its current name."
            ),
        ),
    ],
    identifier: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("identifier", "file_path"),
            description="The note to move: a path, permalink, memory:// URL or exact title.",
        ),
    ] = None,
    path: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("path", "dir_name", "folder"),
            description="The directory to move, with everything in it, e.g. 'drafts/2025'.",
        ),
    ] = None,
    expected_checksum: Annotated[
        str | None,
        Field(
            min_length=1,
            description="Move the note only while it is still this revision (from bm_read).",
        ),
    ] = None,
    output_format: OutputFormat = "text",
    *,
    project: ProjectParam,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Move one note or one directory."""
    # --- Argument combinations ---
    if (identifier is None) == (path is None):
        raise refusal(
            "INVALID_ARGUMENTS",
            "Give `identifier` to move a note or `path` to move a directory, exactly one.",
            output_format=output_format,
        )
    if path is not None and expected_checksum is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "expected_checksum guards a single note; a directory move cannot take one.",
            output_format=output_format,
        )

    source = identifier if identifier is not None else path
    assert source is not None
    # A note is resolved by identifier; a directory is a path that must stay inside.
    kind = "identifier" if identifier is not None else "path"
    async with open_project_target(
        source, project, context, kind=kind, output_format=output_format
    ) as target:
        destination_path = _project_relative_destination(target, destination, output_format)
        other_project = await project_named_by_destination(
            target.client, destination_path, target.project
        )
        if other_project is not None:
            raise refusal(
                "UNSUPPORTED",
                f"'{destination}' starts with the project '{other_project}'; moves stay inside "
                f"project '{target.project.name}'.",
                try_instead=(
                    f"bm_read the note, bm_write it with project='{other_project}', then "
                    "bm_delete the original"
                ),
                output_format=output_format,
            )
        if identifier is not None:
            return await _move_note(
                target, identifier, destination_path, expected_checksum, output_format
            )
        return await _move_directory(target, destination_path, output_format)


def _project_relative_destination(
    target: ProjectTarget, destination: str, output_format: OutputFormat
) -> str:
    """The destination as a project-relative path, keeping a trailing '/'.

    A caller who addressed the source as '<project>/path' gets paths back in that
    form, so a destination spelled the same way names the same project and drops
    its prefix here.
    """
    # Path normalization drops a trailing separator, which is meaningful here.
    into_folder = destination.endswith(("/", "\\"))
    relative = PureWindowsPath(memory_url_path(destination)).as_posix().lstrip("/")
    if relative in {"", "."}:
        relative = ""
    # The leading segments name the project when they normalize the way routing
    # does, so 'My Research/archive/' and 'my-research/archive/' both qualify, and
    # a Cloud route's 'team/research/' prefix is matched whole. 'research/' alone
    # is that project's root: normalization dropped its slash.
    if target.route.stripped and target.route.project is not None:
        prefix = [generate_permalink(part) for part in target.route.project.split("/")]
        segments = relative.split("/")
        leading = [generate_permalink(part) for part in segments[: len(prefix)]]
        remainder = segments[len(prefix) :]
        if leading == prefix and (remainder or into_folder):
            relative = "/".join(remainder)
    if not validate_project_path(
        relative.rstrip("/"), local_project_path(target.client, target.project)
    ):
        raise refusal(
            "OUTSIDE_PROJECT",
            f"Destination '{destination}' is outside the project.",
            try_instead="a path relative to the project root, such as 'archive/plan.md'",
            output_format=output_format,
        )
    # Into the root keeps its '/', so both note and directory moves keep their names.
    return f"{relative}/" if into_folder else relative


# --- Notes ---


async def _move_note(
    target: ProjectTarget,
    identifier: str,
    destination: str,
    expected_checksum: str | None,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    note = await resolve_note(
        target,
        identifier,
        output_format=output_format,
        expected_checksum=expected_checksum,
        unchanged="Nothing was moved.",
    )
    source = PurePosixPath(note.file_path)

    # Trigger: the destination ends with '/', or is the project root.
    # Why: one parameter covers both "move into this folder" and "move to this path".
    # Outcome: the note keeps its file name under the destination folder.
    if destination.endswith("/") or not destination:
        destination_path = PurePosixPath(destination.rstrip("/")) / source.name
    else:
        destination_path = PurePosixPath(destination)
    _check_note_destination(
        source, destination_path, destination, target.paths.path(note.file_path), output_format
    )

    try:
        moved = await KnowledgeClient(target.client, target.project.external_id).move_entity(
            note.external_id, str(destination_path), base_checksum=expected_checksum
        )
    except ToolError as error:
        raise note_change_failure(
            error,
            action="move",
            address=target.paths.path(note.file_path),
            unchanged="Nothing was moved.",
            occupied=target.paths.path(str(destination_path)),
            project=target.project.external_id,
            output_format=output_format,
        ) from error

    # The API moves into an existing folder's spelling; the result then names the
    # requested path next to the actual one.
    change = NoteChange.of(
        moved,
        action="moved",
        paths=target.paths,
        requested_file_path=str(destination_path),
        previous_file_path=note.file_path,
    )
    return change.as_json() if output_format == "json" else "\n".join(change.text_lines())


def _check_note_destination(
    source: PurePosixPath,
    destination_path: PurePosixPath,
    destination: str,
    address: str,
    output_format: OutputFormat,
) -> None:
    """Refuse a note destination that is the same file or another kind of file."""
    if destination_path == source:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"The note is already at '{address}'.",
            output_format=output_format,
        )
    # Trigger: no extension, as in 'archive/2025'.
    # Why: that could be a folder meant with a trailing '/', or a new file name;
    #      guessing wrong renames the note.
    # Outcome: the refusal spells out both readings.
    if not destination_path.suffix:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"'{destination}' has no file extension.",
            try_instead=(
                f"'{destination}/' to move into that folder, or "
                f"'{destination_path.with_suffix(source.suffix)}' to rename the note"
            ),
            output_format=output_format,
        )
    # '.md' and '.markdown' are one file type; any other change of type is refused.
    both_markdown = {source.suffix.lower(), destination_path.suffix.lower()} <= (
        RUNTIME_MARKDOWN_FILE_SUFFIXES
    )
    if destination_path.suffix.lower() != source.suffix.lower() and not both_markdown:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"A move keeps the file type: '{source.name}' cannot become '{destination_path.name}'.",
            try_instead=f"'{destination_path.with_suffix(source.suffix)}'",
            output_format=output_format,
        )


# --- Directories ---


async def _move_directory(
    target: ProjectTarget, destination: str, output_format: OutputFormat
) -> str | dict[str, Any]:
    source = memory_url_path(target.route.path).strip("/")
    # A trailing '/' moves the directory into that folder, as it does for a note.
    if destination.endswith("/"):
        destination = f"{destination}{PurePosixPath(source).name}"
    destination = destination.strip("/")
    if not source:
        raise refusal(
            "INVALID_ARGUMENTS",
            "The project root cannot be moved.",
            output_format=output_format,
        )
    if not destination:
        raise refusal(
            "INVALID_ARGUMENTS",
            "A directory cannot move to the project root; name its new path.",
            output_format=output_format,
        )
    # 'archive/' or '/' for a directory already there names its current path.
    if destination == source:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"'{target.paths.path(source)}' is already there; name a different destination.",
            output_format=output_format,
        )
    try:
        moved = await KnowledgeClient(target.client, target.project.external_id).move_directory(
            source, destination
        )
    except ToolError as error:
        raise note_change_failure(
            error,
            action="move",
            address=target.paths.path(source),
            unchanged="Nothing was moved.",
            occupied=target.paths.path(destination),
            output_format=output_format,
            subject="directory",
            project=target.project.external_id,
        ) from error

    if moved.total_files == 0:
        raise refusal(
            "NOT_FOUND",
            f"No files under '{target.paths.path(source)}' in project '{target.project.name}'.",
            try_instead=(
                f"bm_list(path={target.paths.path(source.rpartition('/')[0]) or '/'!r}, "
                f"project={target.project_ref!r})"
            ),
            output_format=output_format,
        )
    # The server may have kept an existing folder's spelling ('Archive' for
    # 'archive'); report the folder the files are in. A server older than the
    # field reports none, and the requested spelling is the best there is.
    written_destination = moved.destination if moved.destination is not None else destination
    result: dict[str, Any] = {
        "action": "moved",
        "path": target.paths.path(source),
        "destination": target.paths.path(written_destination),
        "total_files": moved.total_files,
        "moved_files": [target.paths.path(file) for file in moved.moved_files],
        "failed": [
            {"path": target.paths.path(failure.path), "error": failure.error}
            for failure in moved.errors
        ],
    }
    # As a note move does, say when the folder differs from the one asked for.
    if written_destination != destination:
        result["requested_destination"] = target.paths.path(destination)
    # Trigger: some files moved and some did not.
    # Why: the move is neither done nor undone; the caller must see which files
    #      already moved before deciding what to retry.
    # Outcome: an error carrying the full summary.
    if moved.failed_moves:
        raise refusal(
            "PARTIAL_FAILURE",
            f"Moved {moved.successful_moves} of {moved.total_files} files from "
            f"'{result['path']}' to '{result['destination']}'; {moved.failed_moves} failed: "
            + "; ".join(f"{failure['path']}: {failure['error']}" for failure in result["failed"]),
            try_instead=(
                f"bm_list(path={result['destination']!r}, project={target.project_ref!r}) "
                "to see what moved"
            ),
            output_format=output_format,
        )
    if output_format == "json":
        return result
    return (
        f"Moved directory {result['path']} to {result['destination']} ({moved.total_files} files)"
    )
