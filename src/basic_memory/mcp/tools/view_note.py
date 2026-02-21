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
    workspace: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
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
        page: Page number for paginated results (default: 1)
        page_size: Number of items per page (default: 10)
        context: Optional FastMCP context for performance caching.

    Returns:
        Instructions for Claude to create a markdown artifact with the note content.

    Examples:
        # View a note by title
        view_note("Meeting Notes")

        # View a note by permalink
        view_note("meetings/weekly-standup")

        # View with pagination
        view_note("large-document", page=2, page_size=5)

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
            workspace=workspace,
            page=page,
            page_size=page_size,
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
