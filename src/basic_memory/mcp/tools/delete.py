"""bm_delete: delete a note or a directory.

Deleting a note that does not exist is an error: the caller asked for a change
that did not happen. A directory delete must say `recursive=true`, because it
removes every file under the directory.
"""

from typing import Annotated, Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, Field

from basic_memory.indexing.directory_delete_runner import normalize_directory_delete_path
from basic_memory.mcp.clients import DirectoryClient, KnowledgeClient
from basic_memory.mcp.note_targets import (
    ProjectTarget,
    open_project_target,
    resolve_note,
)
from basic_memory.mcp.refusals import note_change_failure, refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import OutputFormat, ProjectParam
from basic_memory.schemas.memory import memory_url_path


@mcp.tool(
    name="bm_delete",
    title="Delete note",
    description=(
        "Delete a Basic Memory note (`identifier`) or a directory (`path`). A directory "
        "delete removes every file under it and needs `recursive=true`. Pass "
        "`expected_checksum` (from bm_read) to delete a note only if it is still the "
        "revision you read. A note that does not exist is an error. This cannot be undone."
    ),
    tags={"notes"},
    annotations={
        "title": "Delete note",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_delete(
    identifier: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("identifier", "file_path"),
            description="The note to delete: a path, permalink, memory:// URL or exact title.",
        ),
    ] = None,
    path: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("path", "dir_name", "folder"),
            description="The directory to delete, with everything in it.",
        ),
    ] = None,
    recursive: Annotated[
        bool, Field(description="Required true to delete a directory and everything under it.")
    ] = False,
    expected_checksum: Annotated[
        str | None,
        Field(
            min_length=1,
            description="Delete the note only while it is still this revision (from bm_read).",
        ),
    ] = None,
    output_format: OutputFormat = "text",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Delete one note or one directory."""
    # --- Argument combinations ---
    if (identifier is None) == (path is None):
        raise refusal(
            "INVALID_ARGUMENTS",
            "Give `identifier` to delete a note or `path` to delete a directory, exactly one.",
            output_format=output_format,
        )
    if path is not None and expected_checksum is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "expected_checksum guards a single note; a directory delete cannot take one.",
            output_format=output_format,
        )
    if identifier is not None and recursive:
        raise refusal(
            "INVALID_ARGUMENTS",
            "recursive applies to directories; give `path` to delete one.",
            output_format=output_format,
        )

    source = identifier if identifier is not None else path
    assert source is not None
    # Directory deletes strip whitespace at the API boundary. Do it before routing too:
    # Windows resolves ' / ' outside the project before the root-delete guard can run.
    if identifier is None:
        source = source.strip()
    # A note is resolved by identifier; a directory is a path that must stay inside.
    kind = "identifier" if identifier is not None else "path"
    async with open_project_target(
        source, project, context, kind=kind, output_format=output_format
    ) as target:
        if identifier is not None:
            return await _delete_note(target, identifier, expected_checksum, output_format)
        return await _delete_directory(target, recursive, output_format)


async def _delete_note(
    target: ProjectTarget,
    identifier: str,
    expected_checksum: str | None,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    note = await resolve_note(
        target,
        identifier,
        output_format=output_format,
        expected_checksum=expected_checksum,
        unchanged="Nothing was deleted.",
    )
    try:
        deleted = await KnowledgeClient(target.client, target.project.external_id).delete_entity(
            note.external_id, base_checksum=expected_checksum
        )
    except ToolError as error:
        raise note_change_failure(
            error,
            action="delete",
            address=target.paths.path(note.file_path),
            unchanged="Nothing was deleted.",
            output_format=output_format,
        ) from error
    file_path = target.paths.path(note.file_path)
    # Trigger: the note resolved, then was gone when the delete arrived.
    # Why: another writer removed it first; this call deleted nothing (C11).
    # Outcome: NOT_FOUND, as if it had been missing all along.
    if not deleted.deleted:
        raise refusal(
            "NOT_FOUND",
            f"'{file_path}' was already deleted.",
            output_format=output_format,
        )
    if output_format == "json":
        return {"action": "deleted", "title": note.title, "file_path": file_path}
    return f"Deleted {file_path}"


async def _delete_directory(
    target: ProjectTarget, recursive: bool, output_format: OutputFormat
) -> str | dict[str, Any]:
    # The API normalizes the path this way before deleting, and an empty path is
    # the whole project, so the root check runs on the normalized form (' ' too).
    try:
        directory = normalize_directory_delete_path(memory_url_path(target.route.path))
    except ValueError as error:
        raise refusal(
            "OUTSIDE_PROJECT",
            f"'{target.route.path}' is not a directory inside the project.",
            output_format=output_format,
        ) from error
    shown = target.paths.path(directory)
    if not directory:
        raise refusal(
            "INVALID_ARGUMENTS",
            "The project root cannot be deleted with bm_delete.",
            output_format=output_format,
        )
    # Trigger: a directory delete without recursive=true.
    # Why: the flag is the caller saying "everything under it"; the refusal shows
    #      what that is, so the caller decides knowing the size.
    # Outcome: nothing is deleted; the error names the entry count.
    if not recursive:
        listing = await DirectoryClient(target.client, target.project.external_id).list(
            directory, depth=1, page_size=1
        )
        if listing.total == 0:
            raise refusal(
                "NOT_FOUND",
                f"No directory '{shown}' in project '{target.project.name}'.",
                output_format=output_format,
            )
        raise refusal(
            "INVALID_ARGUMENTS",
            f"'{shown}' is a directory with {listing.total} entries at its top level. "
            "Deleting it removes everything under it.",
            try_instead=f"bm_delete(path='{shown}', recursive=true)",
            output_format=output_format,
        )

    try:
        deleted = await KnowledgeClient(target.client, target.project.external_id).delete_directory(
            directory
        )
    except ToolError as error:
        raise note_change_failure(
            error,
            action="delete",
            address=shown,
            unchanged="Nothing was deleted.",
            output_format=output_format,
            subject="directory",
        ) from error
    if deleted.total_files == 0:
        raise refusal(
            "NOT_FOUND",
            f"No files under '{shown}' in project '{target.project.name}'.",
            output_format=output_format,
        )
    if deleted.failed_deletes:
        raise refusal(
            "PARTIAL_FAILURE",
            f"Deleted {deleted.successful_deletes} of {deleted.total_files} files under "
            f"'{shown}'; {deleted.failed_deletes} failed: "
            + "; ".join(
                f"{target.paths.path(failure.path)}: {failure.error}" for failure in deleted.errors
            ),
            try_instead=f"bm_list(path='{shown}') to see what is left",
            output_format=output_format,
        )
    if output_format == "json":
        return {
            "action": "deleted",
            "path": shown,
            "total_files": deleted.total_files,
            "deleted_files": [target.paths.path(file) for file in deleted.deleted_files],
        }
    return f"Deleted directory {shown} ({deleted.total_files} files)"
