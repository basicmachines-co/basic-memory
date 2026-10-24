"""View note tool for Basic Memory MCP server."""

from textwrap import dedent
from typing import Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.read import bm_read


@mcp.tool(
    title="View Note",
    description=(
        "Read a note (same resolution as bm_read) and return it wrapped in an instruction "
        "to display it to the user as a markdown artifact. Use when the user wants to see the "
        "note rendered; use bm_read when you need the content yourself. Missing notes raise tool errors."
    ),
    tags={"notes"},
    annotations={
        "title": "View Note",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def view_note(
    identifier: str,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """View a markdown note as a formatted artifact.

    This tool reads a note using the same logic as bm_read but instructs Claude
    to display the content as a markdown artifact in the Claude Desktop app.
    Project parameter optional with server resolution.

    Args:
        identifier: The title or permalink of the note to view
        project: Project name to read from. Optional - server will resolve using hierarchy.
                If unknown, use bm_project(command="list") to discover available projects.
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from bm_project(command="list").
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
    logger.debug(f"Viewing note: {identifier} in project: {project}")

    # Call the existing bm_read logic (default output_format="text" returns str)
    content = str(
        await bm_read(
            identifier=identifier,
            project=project_id or project,
            context=context,
        )
    )

    # The note opens with its own `---` frontmatter fence, so the content is set off by
    # named markers that cannot collide with it. The header is joined, not dedented:
    # dedent sees the note's unindented lines and would leave the header indented.
    header = dedent(f"""
        Note retrieved: "{identifier}"

        Display this note as a markdown artifact for the user.

        Content:
        """).strip()
    return f"{header}\n<note>\n{content.strip()}\n</note>"
