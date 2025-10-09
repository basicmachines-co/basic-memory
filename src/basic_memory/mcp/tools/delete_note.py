from textwrap import dedent
from typing import Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.project_context import get_active_project
from basic_memory.mcp.tools.utils import call_delete
from basic_memory.mcp.server import mcp
from basic_memory.mcp.async_client import get_client
from basic_memory.schemas import DeleteEntitiesResponse


def _format_delete_error_response(project: str, error_message: str, identifier: str) -> str:
    """Format helpful error responses for delete failures that guide users to successful deletions."""

    # Note not found errors
    if "entity not found" in error_message.lower() or "not found" in error_message.lower():
        search_term = identifier.split("/")[-1] if "/" in identifier else identifier
        title_format = (
            identifier.split("/")[-1].replace("-", " ").title() if "/" in identifier else identifier
        )
        permalink_format = identifier.lower().replace(" ", "-")

        return dedent(f"""
            # Delete Failed - Note Not Found

            The note '{identifier}' could not be found for deletion in {project}.

            ## This might mean:
            1. **Already deleted**: The note may have been deleted previously
            2. **Wrong identifier**: The identifier format might be incorrect
            3. **Different project**: The note might be in a different project

            ## How to verify:
            1. **Search for the note**: Use `search_notes("{project}", "{search_term}")` to find it
            2. **Try different formats**:
               - If you used a permalink like "folder/note-title", try just the title: "{title_format}"
               - If you used a title, try the permalink format: "{permalink_format}"

            3. **Check if already deleted**: Use `list_directory("/")` to see what notes exist
            4. **List notes in project**: Use `list_directory("/")` to see what notes exist in the current project

            ## If the note actually exists:
            ```
            # First, find the correct identifier:
            search_notes("{project}", "{identifier}")

            # Then delete using the correct identifier:
            delete_note("{project}", "correct-identifier-from-search")
            ```

            ## If you want to delete multiple similar notes:
            Use search to find all related notes and delete them one by one.
            """).strip()

    # Permission/access errors
    if (
        "permission" in error_message.lower()
        or "access" in error_message.lower()
        or "forbidden" in error_message.lower()
    ):
        return f"""# Delete Failed - Permission Error

You don't have permission to delete '{identifier}': {error_message}

## How to resolve:
1. **Check permissions**: Verify you have delete/write access to this project
2. **File locks**: The note might be open in another application
3. **Project access**: Ensure you're in the correct project with proper permissions

## Alternative actions:
- List available projects: `list_memory_projects()`
- Specify the correct project: `delete_note("{identifier}", project="project-name")`
- Verify note exists first: `read_note("{identifier}", project="project-name")`

## If you have read-only access:
Ask someone with write access to delete the note."""

    # Server/filesystem errors
    if (
        "server error" in error_message.lower()
        or "filesystem" in error_message.lower()
        or "disk" in error_message.lower()
    ):
        return f"""# Delete Failed - System Error

A system error occurred while deleting '{identifier}': {error_message}

## Immediate steps:
1. **Try again**: The error might be temporary
2. **Check file status**: Verify the file isn't locked or in use
3. **Check disk space**: Ensure the system has adequate storage

## Troubleshooting:
- Verify note exists: `read_note("{project}","{identifier}")`
- Try again in a few moments

## If problem persists:
Send a message to support@basicmachines.co - there may be a filesystem or database issue."""

    # Database/sync errors
    if "database" in error_message.lower() or "sync" in error_message.lower():
        return f"""# Delete Failed - Database Error

A database error occurred while deleting '{identifier}': {error_message}

## This usually means:
1. **Sync conflict**: The file system and database are out of sync
2. **Database lock**: Another operation is accessing the database
3. **Corrupted entry**: The database entry might be corrupted

## Steps to resolve:
1. **Try again**: Wait a moment and retry the deletion
2. **Check note status**: `read_note("{project}","{identifier}")` to see current state
3. **Manual verification**: Use `list_directory()` to see if file still exists

## If the note appears gone but database shows it exists:
Send a message to support@basicmachines.co - a manual database cleanup may be needed."""

    # Generic fallback
    return f"""# Delete Failed

Error deleting note '{identifier}': {error_message}

## General troubleshooting:
1. **Verify the note exists**: `read_note("{project}", "{identifier}")` or `search_notes("{project}", "{identifier}")`
2. **Check permissions**: Ensure you can edit/delete files in this project
3. **Try again**: The error might be temporary
4. **Check project**: Make sure you're in the correct project

## Step-by-step approach:
```
# 1. Confirm note exists and get correct identifier
search_notes("{project}", "{identifier}")

# 2. Read the note to verify access
read_note("{project}", "correct-identifier-from-search")

# 3. Try deletion with correct identifier
delete_note("{project}", "correct-identifier-from-search")
```

## Alternative approaches:
- Check what notes exist: `list_directory("{project}", "/")`

## Need help?
If the note should be deleted but the operation keeps failing, send a message to support@basicmemory.com."""


@mcp.tool(description="Delete a note by title or permalink")
async def delete_note(
    identifier: str, project: Optional[str] = None, context: Context | None = None
) -> bool | str:
    """Delete a note from the knowledge base.

    Permanently removes a note from the specified project. The note is identified
    by title or permalink. If the note doesn't exist, the operation returns False
    without error. If deletion fails due to other issues, helpful error messages are provided.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode → project parameter → default project.
    If project unknown, use list_memory_projects() or recent_activity() first.

    Args:
        project: Project name to delete from. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        identifier: Note title or permalink to delete
                   Can be a title like "Meeting Notes" or permalink like "notes/meeting-notes"
        context: Optional FastMCP context for performance caching.

    Returns:
        True if note was successfully deleted, False if note was not found.
        On errors, returns a formatted string with helpful troubleshooting guidance.

    Examples:
        # Delete by title
        delete_note("my-project", "Meeting Notes: Project Planning")

        # Delete by permalink
        delete_note("work-docs", "notes/project-planning")

        # Delete with exact path
        delete_note("research", "experiments/ml-model-results")

        # Common usage pattern
        if delete_note("my-project", "old-draft"):
            print("Note deleted successfully")
        else:
            print("Note not found or already deleted")

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        SecurityError: If identifier attempts path traversal

    Warning:
        This operation is permanent and cannot be undone. The note file
        will be removed from the filesystem and all references will be lost.

    Note:
        If the note is not found, this function provides helpful error messages
        with suggestions for finding the correct identifier, including search
        commands and alternative formats to try.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        project_url = active_project.project_url

        try:
            response = await call_delete(client, f"{project_url}/knowledge/entities/{identifier}")
            result = DeleteEntitiesResponse.model_validate(response.json())

            if result.deleted:
                logger.info(
                    f"Successfully deleted note: {identifier} in project: {active_project.name}"
                )
                return True
            else:
                logger.warning(f"Delete operation completed but note was not deleted: {identifier}")
                return False

        except Exception as e:  # pragma: no cover
            logger.error(f"Delete failed for '{identifier}': {e}, project: {active_project.name}")
            # Return formatted error message for better user experience
            return _format_delete_error_response(active_project.name, str(e), identifier)
