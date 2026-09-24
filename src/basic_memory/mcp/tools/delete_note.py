from textwrap import dedent
from typing import Any, Annotated, Optional, Literal

from loguru import logger
from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, Field

from basic_memory.config import ConfigManager
from basic_memory.mcp.project_context import (
    detect_project_from_memory_url_prefix,
    get_project_client,
    resolve_project_and_path,
)
from basic_memory.mcp.server import mcp
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.utils import generate_permalink, normalize_project_reference
from basic_memory.workspace_context import current_workspace_permalink_context


def _format_delete_error_response(project: str, error_message: str, identifier: str) -> str:
    """Format delete failures as facts plus keyword-exact recovery calls.

    Every suggested call spells out its keywords: the first positional parameter of
    delete_note is the note identifier, so a positional hint could delete the wrong note.
    """
    lowered = error_message.lower()

    # Note not found errors
    if "entity not found" in lowered or "not found" in lowered:
        search_term = identifier.split("/")[-1] if "/" in identifier else identifier
        title_format = (
            identifier.split("/")[-1].replace("-", " ").title() if "/" in identifier else identifier
        )
        permalink_format = identifier.lower().replace(" ", "-")

        return dedent(f"""
            # Delete Failed - Note Not Found

            The note '{identifier}' could not be found for deletion in {project}.
            It may already be deleted, the identifier may not match exactly, or the note
            may live in a different project.

            delete_note needs an exact title or permalink. Alternate forms of this
            identifier: title "{title_format}", permalink "{permalink_format}".

            Find the exact identifier with
            `search_notes(query="{search_term}", project="{project}")` or
            `list_directory(dir_name="/", project="{project}")`, then retry
            `delete_note(identifier="...", project="{project}")`.
            """).strip()

    # Permission/access errors
    if "permission" in lowered or "access" in lowered or "forbidden" in lowered:
        return dedent(f"""
            # Delete Failed - Permission Error

            No write access to delete '{identifier}' in {project}: {error_message}

            The note may be open or locked by another application, or this account may
            have read-only access to the project. `list_memory_projects()` shows the
            projects this account can reach.
            """).strip()

    # Server/filesystem errors
    if "server error" in lowered or "filesystem" in lowered or "disk" in lowered:
        return dedent(f"""
            # Delete Failed - System Error

            A system error occurred while deleting '{identifier}' in {project}: {error_message}

            The error may be transient (a locked file or low disk space). Check whether the
            note still exists with `read_note(identifier="{identifier}", project="{project}")`
            before retrying. If it persists, contact support@basicmemory.com.
            """).strip()

    # Database/sync errors
    if "database" in lowered or "sync" in lowered:
        return dedent(f"""
            # Delete Failed - Database Error

            A database error occurred while deleting '{identifier}' in {project}: {error_message}

            The file and the index may be out of sync, or another operation may hold a
            database lock. Check the note's current state with
            `read_note(identifier="{identifier}", project="{project}")` before retrying. If
            the file is gone but the index still lists it, contact support@basicmemory.com.
            """).strip()

    # Generic fallback
    return dedent(f"""
        # Delete Failed

        Error deleting note '{identifier}' in {project}: {error_message}

        Confirm the exact identifier with
        `search_notes(query="{identifier}", project="{project}")` before retrying. If the
        operation keeps failing, contact support@basicmemory.com.
        """).strip()


def _directory_path_for_delete(
    target_identifier: str,
    active_project: ProjectItem,
    *,
    include_project_prefix: bool,
) -> str:
    """Return the project-relative directory path expected by the delete API."""
    directory = normalize_project_reference(target_identifier).strip("/")
    project_permalink = active_project.permalink

    route_prefixes: list[str] = []
    workspace_context = current_workspace_permalink_context()
    if workspace_context and workspace_context.should_prefix_permalinks:
        route_prefixes.append(
            f"{generate_permalink(workspace_context.workspace_slug)}/{project_permalink}"
        )
    if include_project_prefix:
        route_prefixes.append(project_permalink)

    for route_prefix in route_prefixes:
        if directory.startswith(f"{route_prefix}/"):
            return directory.removeprefix(f"{route_prefix}/")

    return directory


@mcp.tool(
    title="Delete Note",
    description=(
        "Delete a note or directory by title, permalink, or path. Deletion is permanent "
        "and removes the file. With is_directory=True every file under the path is "
        "deleted. A missing note returns false rather than an error."
    ),
    tags={"notes"},
    annotations={
        "title": "Delete Note",
        "readOnlyHint": False,
        "destructiveHint": True,
        "openWorldHint": False,
    },
)
async def delete_note(
    identifier: str,
    is_directory: Annotated[
        bool,
        Field(default=False, validation_alias=AliasChoices("is_directory", "is_dir")),
    ] = False,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> bool | str | dict[str, Any]:
    """Delete a note or directory from the knowledge base.

    Permanently removes a note or directory from the specified project. For single notes,
    they are identified by title or permalink. For directories, use is_directory=True and
    provide the directory path. If the note/directory doesn't exist, the operation returns
    False without error. If deletion fails, helpful error messages are provided.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode → project parameter → default project.
    If project unknown, use list_memory_projects() or recent_activity() first.

    Args:
        identifier: For files: note title or permalink to delete.
                   For directories: the directory path (e.g., "docs", "projects/2025").
                   Can be a title like "Meeting Notes" or permalink like "notes/meeting-notes"
        is_directory: If True, deletes an entire directory and all its contents.
                     When True, identifier should be a directory path
                     (without file extensions). Defaults to False.
        project: Project name to delete from. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from list_memory_projects().
        output_format: "text" returns true/false for a single note (false if not found),
            a markdown summary for directories, or markdown guidance on error. "json"
            returns machine-readable deletion metadata.
        context: Optional FastMCP context for performance caching.

    Returns:
        True if note was successfully deleted, False if note was not found.
        For directories, returns a formatted summary of deleted files.
        On errors, returns a formatted string with helpful troubleshooting guidance.

    Examples:
        # Delete by title
        delete_note("Meeting Notes: Project Planning")

        # Delete by permalink
        delete_note("notes/project-planning")

        # Delete with explicit project
        delete_note("experiments/ml-model-results", project="research")

        # Delete entire directory
        delete_note("docs", is_directory=True)

        # Delete nested directory
        delete_note("projects/2024", is_directory=True)

        # Common usage pattern
        if delete_note("old-draft"):
            print("Note deleted successfully")
        else:
            print("Note not found or already deleted")

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        SecurityError: If identifier attempts path traversal

    Warning:
        This operation is permanent and cannot be undone. The note/directory files
        will be removed from the filesystem and all references will be lost.

    Note:
        If the note is not found, this function provides helpful error messages
        with suggestions for finding the correct identifier, including search
        commands and alternative formats to try.
    """
    # Detect project from memory URL prefix before routing
    # Trigger: identifier starts with memory:// and no explicit project/project_id was provided
    # Why: only gate on memory:// to avoid misrouting plain paths like "research/note"
    #      where "research" is a directory, not a project name
    # Outcome: project is set from the URL prefix, routing goes to the correct project
    if project is None and project_id is None and identifier.strip().startswith("memory://"):
        detected = await detect_project_from_memory_url_prefix(
            identifier,
            ConfigManager().config,
            context=context,
        )
        if detected is not None:
            # The id rides along so the name is never re-resolved against a
            # different accessible workspace holding the same permalink (#1432).
            project, project_id = detected.project, detected.project_id

    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        logger.debug(
            f"Deleting {'directory' if is_directory else 'note'}: {identifier} in project: {active_project.name}"
        )

        # Import here to avoid circular import
        from basic_memory.mcp.clients import KnowledgeClient

        # Use typed KnowledgeClient for API calls
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        _, target_identifier, is_memory_url = await resolve_project_and_path(
            client,
            identifier,
            active_project.name,
            context,
            strict_project_routing=True,
            allow_missing_project_fallback=True,
        )

        # Handle directory deletes
        if is_directory:
            try:
                # Trigger: directory input was routed from a memory:// URL.
                # Why: resolve_project_and_path returns canonical permalinks, while
                #   delete_directory filters by project-relative file_path prefixes.
                # Outcome: strip only the route prefix before calling the delete API.
                directory_identifier = (
                    _directory_path_for_delete(
                        target_identifier,
                        active_project,
                        include_project_prefix=ConfigManager().config.permalinks_include_project,
                    )
                    if is_memory_url
                    else target_identifier
                )
                result = await knowledge_client.delete_directory(directory_identifier)
                if output_format == "json":
                    response = {
                        "deleted": result.total_files > 0 and result.failed_deletes == 0,
                        "is_directory": True,
                        "identifier": identifier,
                        "total_files": result.total_files,
                        "successful_deletes": result.successful_deletes,
                        "failed_deletes": result.failed_deletes,
                        "deleted_files": result.deleted_files,
                        "errors": [error.model_dump() for error in result.errors],
                    }
                    if result.total_files == 0:
                        response["error"] = "Directory not found or empty: no files matched"
                    elif result.failed_deletes > 0:
                        response["error"] = (
                            "Directory delete incomplete: "
                            f"{result.failed_deletes} of {result.total_files} file(s) failed"
                        )
                    return response

                if result.total_files == 0:
                    return f"""# Directory Delete Failed - No Files Found

No files found for directory `{identifier}`.
Total files: 0.

<!-- Project: {active_project.name} -->"""

                # Build success message for directory delete
                result_lines = [
                    "# Directory Deleted Successfully",
                    "",
                    f"**Directory:** `{identifier}`",
                    "",
                    "## Summary",
                    f"- Total files: {result.total_files}",
                    f"- Successfully deleted: {result.successful_deletes}",
                    f"- Failed: {result.failed_deletes}",
                ]

                if result.deleted_files:
                    result_lines.extend(["", "## Deleted Files"])
                    for file_path in result.deleted_files[:10]:  # Show first 10
                        result_lines.append(f"- `{file_path}`")
                    if len(result.deleted_files) > 10:
                        result_lines.append(f"- ... and {len(result.deleted_files) - 10} more")

                if result.errors:  # pragma: no cover
                    result_lines.extend(["", "## Errors"])
                    for error in result.errors[:5]:  # Show first 5 errors
                        result_lines.append(f"- `{error.path}`: {error.error}")
                    if len(result.errors) > 5:
                        result_lines.append(f"- ... and {len(result.errors) - 5} more errors")

                result_lines.extend(["", f"<!-- Project: {active_project.name} -->"])

                logger.info(
                    f"Directory delete completed: {identifier}, "
                    f"deleted={result.successful_deletes}, failed={result.failed_deletes}"
                )

                return "\n".join(result_lines)

            except Exception as e:  # pragma: no cover
                logger.error(f"Directory delete failed for '{identifier}': {e}")
                if output_format == "json":
                    return {
                        "deleted": False,
                        "is_directory": True,
                        "identifier": identifier,
                        "total_files": 0,
                        "successful_deletes": 0,
                        "failed_deletes": 0,
                        "error": str(e),
                    }
                return f"""# Directory Delete Failed

Error deleting directory '{identifier}': {str(e)}

## Troubleshooting:
1. **Verify the directory exists**: Use `list_directory("{identifier}")` to check
2. **Check for permission issues**: Ensure you have delete access to the project
3. **Try individual deletes**: Delete files one at a time if bulk delete fails

## Alternative approach:
```
# List directory contents first
list_directory("{identifier}")

# Then delete individual files
delete_note("path/to/file.md")
```"""

        # Handle single note deletes
        note_title = None
        note_permalink = None
        note_file_path = None
        try:
            # Resolve identifier to entity ID
            entity_id = await knowledge_client.resolve_entity(target_identifier, strict=True)
            if output_format == "json":
                entity = await knowledge_client.get_entity(entity_id)
                note_title = entity.title
                note_permalink = entity.permalink
                note_file_path = entity.file_path
        except ToolError as e:
            # If entity not found, return False (note doesn't exist)
            if "Entity not found" in str(e) or "not found" in str(e).lower():
                logger.warning(f"Note not found for deletion: {identifier}")
                if output_format == "json":
                    return {
                        "deleted": False,
                        "title": None,
                        "permalink": None,
                        "file_path": None,
                    }
                return False
            # For other resolution errors, return formatted error message
            logger.error(  # pragma: no cover
                f"Delete failed for '{identifier}': {e}, project: {active_project.name}"
            )
            if output_format == "json":
                return {
                    "deleted": False,
                    "title": None,
                    "permalink": None,
                    "file_path": None,
                    "error": str(e),
                }
            return _format_delete_error_response(  # pragma: no cover
                active_project.name, str(e), identifier
            )

        try:
            # Call the DELETE endpoint
            result = await knowledge_client.delete_entity(entity_id)

            if result.deleted:
                logger.info(
                    f"Successfully deleted note: {identifier} in project: {active_project.name}"
                )
                if output_format == "json":
                    return {
                        "deleted": True,
                        "title": note_title,
                        "permalink": note_permalink,
                        "file_path": note_file_path,
                    }
                return True
            else:
                logger.warning(  # pragma: no cover
                    f"Delete operation completed but note was not deleted: {identifier}"
                )
                if output_format == "json":
                    return {
                        "deleted": False,
                        "title": note_title,
                        "permalink": note_permalink,
                        "file_path": note_file_path,
                    }
                return False  # pragma: no cover

        except Exception as e:  # pragma: no cover
            logger.error(f"Delete failed for '{identifier}': {e}, project: {active_project.name}")
            if output_format == "json":
                return {
                    "deleted": False,
                    "title": note_title,
                    "permalink": note_permalink,
                    "file_path": note_file_path,
                    "error": str(e),
                }
            # Return formatted error message for better user experience
            return _format_delete_error_response(active_project.name, str(e), identifier)
