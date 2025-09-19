"""View note tool for Basic Memory MCP server."""

from textwrap import dedent
from typing import Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.read_note import read_note


@mcp.tool(
    description="View a note as a formatted artifact for better readability.",
)
async def view_note(
    identifier: str,
    project: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    context: Context | None = None,
) -> str:
    """View a markdown note as a formatted artifact.

    This tool reads a note using the same logic as read_note but displays the content
    as a markdown artifact for better viewing experience in Claude Desktop.
    Project parameter optional with server resolution.

    After calling this tool, create an artifact using the returned content to display
    the note in a readable format. The tool returns the note content that should be
    used to create a markdown artifact.

    Args:
        identifier: The title or permalink of the note to view
        project: Project name to read from. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        page: Page number for paginated results (default: 1)
        page_size: Number of items per page (default: 10)
        context: Optional FastMCP context for performance caching.

    Returns:
        The note content as a markdown artifact with a confirmation message.

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

    # Call the existing read_note logic
    content = await read_note.fn(identifier, project, page, page_size, context)

    # Check if this is an error message (note not found)
    if "# Note Not Found" in content:
        return content  # Return error message directly instead of creating artifact

    # Extract title from content if possible
    title = identifier
    lines = content.split("\n")
    for line in lines:
        if line.startswith("title:"):
            # Extract title from frontmatter
            title = line.split("title:", 1)[1].strip().strip('"').strip("'")
            break
        elif line.startswith("# "):
            # Extract title from first heading
            title = line[2:].strip()
            break

    # Return the artifact XML as part of the response
    artifact = f'<artifact identifier="note-{hash(identifier) & 0x7FFFFFFF}" type="text/markdown" title="{title}">\n{content}\n</artifact>'

    return dedent(f"""
            <instructions>
            Create an artifact using the returned content to display the note in a readable format.
            </instructions>
            {artifact}\n\n✅ Note displayed as artifact: **{title}**""")
