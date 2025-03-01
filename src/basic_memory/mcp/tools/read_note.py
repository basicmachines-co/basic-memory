"""Read note tool for Basic Memory MCP server."""

import logfire
from loguru import logger

from basic_memory.mcp.async_client import client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.memory import memory_url_path


@mcp.tool(
    description="Read a markdown note by title or permalink.",
)
async def read_note(identifier: str, page: int = 1, page_size: int = 10) -> str:
    """Read a markdown note from the knowledge base.

    This tool finds and retrieves a note by its title or permalink, returning
    the raw markdown content including observations, relations, and metadata.
    Unlike read_file, this tool is aware of the knowledge graph structure and
    will attempt to resolve entity references if the file path doesn't exist.

    Args:
        identifier: The title or permalink of the note to read
                   Can be a full memory:// URL, a permalink, or a title
        page: Page number for paginated results (default: 1)
        page_size: Number of items per page (default: 10)

    Returns:
        The full markdown content of the note, either from file content
        or constructed from entity data if direct file access fails.
        For entities without markdown content, returns a message indicating
        the entity was found but has no content.

    Examples:
        # Read by permalink
        read_note("specs/search-spec")

        # Read by title
        read_note("Search Specification")

        # Read with memory URL
        read_note("memory://specs/search-spec")

        # Read with pagination
        read_note("Project Updates", page=2, page_size=5)
    """
    with logfire.span("Reading note", identifier=identifier):  # pyright: ignore [reportGeneralTypeIssues]
        # Get the file via REST API
        entity_path = memory_url_path(identifier)
        path = f"/resource/{entity_path}"
        logger.info(f"Reading note from URL: {path}")

        response = await call_get(client, path, params={"page": page, "page_size": page_size})

        # Just return the content as a string
        if response.status_code == 200:
            return response.text
        else:
            return f"Error: Could not find entity at {identifier}"
