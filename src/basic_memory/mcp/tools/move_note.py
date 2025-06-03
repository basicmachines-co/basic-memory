"""Move note tool for Basic Memory MCP server."""

from typing import Optional

from loguru import logger

from basic_memory.mcp.async_client import client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_post
from basic_memory.mcp.project_session import get_active_project
from basic_memory.schemas import EntityResponse


@mcp.tool(
    description="Move a note to a new location, updating database and maintaining links.",
)
async def move_note(
    identifier: str,
    destination_path: str,
    project: Optional[str] = None,
) -> str:
    """Move a note to a new file location within the same project.

    Args:
        identifier: Entity identifier (title, permalink, or memory:// URL)
        destination_path: New path relative to project root (e.g., "work/meetings/2025-05-26.md")
        project: Optional project name (defaults to current session project)

    Returns:
        Success message with move details

    Examples:
        - Move to new folder: move_note("My Note", "work/notes/my-note.md")
        - Move by permalink: move_note("my-note-permalink", "archive/old-notes/my-note.md") 
        - Specify project: move_note("My Note", "archive/my-note.md", project="work-project")

    Note: This operation moves notes within the specified project only. Moving notes
    between different projects is not currently supported.

    The move operation:
    - Updates the entity's file_path in the database
    - Moves the physical file on the filesystem
    - Optionally updates permalinks if configured
    - Re-indexes the entity for search
    - Maintains all observations and relations
    """
    logger.debug(f"Moving note: {identifier} to {destination_path}")

    active_project = get_active_project(project)
    project_url = active_project.project_url

    # Prepare move request
    move_data = {
        "identifier": identifier,
        "destination_path": destination_path,
        "project": active_project.name,
    }

    # Call the move API endpoint
    url = f"{project_url}/knowledge/move"
    response = await call_post(client, url, json=move_data)
    result = EntityResponse.model_validate(response.json())

    # 10. Build success message
    result_lines = [
        "✅ Note moved successfully",
        "",
        f"📁 **{identifier}** → **{result.file_path}**",
        f"🔗 Permalink: {result.permalink}",
        "📊 Database and search index updated",
        "",
        f"<!-- Project: {active_project.name} -->",
    ]

    # Return the response text which contains the formatted success message
    result = "\n".join(result_lines)

    # Log the operation
    logger.info(
        "Move note completed",
        identifier=identifier,
        destination_path=destination_path,
        project=active_project.name,
        status_code=response.status_code,
    )

    return result
