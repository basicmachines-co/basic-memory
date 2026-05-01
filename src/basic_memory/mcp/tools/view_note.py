"""View note tool for Basic Memory MCP server."""

from textwrap import dedent
from typing import Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.read_note import read_note


@mcp.tool(
    description="View a note as a formatted artifact for better readability.",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def view_note(
    identifier: str,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """View a markdown note as a formatted artifact.

    This tool reads a note using the same logic as read_note but instructs Claude
    to display the content as a markdown artifact in the Claude Desktop app.
    Project parameter optional with server resolution.

    Args:
        identifier: The title or permalink of the note to view
        project: Project name to read from. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from list_memory_projects().
        context: Optional FastMCP context for performance caching.

    Returns:
        Instructions for Claude to create a markdown artifact with the note content.

    Examples:
        # View a note by title
        view_note("Meeting Notes")

        # View a note by permalink
        view_note("meetings/weekly-standup")

        # Explicit project specification
        view_note("Meeting Notes", project="my-project")

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        SecurityError: If identifier attempts path traversal
    """
    logger.info(f"Viewing note: {identifier} in project: {project}")

    # Call the existing read_note logic (default output_format="text" returns str)
    content = str(
        await read_note(
            identifier=identifier,
            project=project,
            project_id=project_id,
            context=context,
        )
    )

    # Check if this is an error message (note not found)
    if "# Note Not Found" in content:
        return content  # Return error message directly

    # Return instructions for Claude to create an artifact
    return dedent(f"""
        Note retrieved: "{identifier}"
        
        Display this note as a markdown artifact for the user.
    
        Content:
        ---
        {content}
        ---
        """).strip()
