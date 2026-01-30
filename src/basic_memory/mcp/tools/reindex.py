"""Reindex tool for Basic Memory MCP server.

This tool allows users to force a full reindex of the search index
without losing or resetting any data.
"""

from typing import Optional

from fastmcp import Context
from loguru import logger

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.project_context import get_active_project
from basic_memory.telemetry import track_mcp_tool


@mcp.tool("force_reindex")
async def force_reindex(
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Force a full reindex of the search index.

    This tool rebuilds the search index from the database without modifying
    or deleting any notes, relations, or observations. Use this when:
    - Search returns empty results for content you know exists
    - Search index appears stale or out of sync
    - After recovering from database issues

    The reindex operation:
    1. Drops the existing search index table
    2. Recreates the FTS5 virtual table
    3. Re-indexes all entities, observations, and relations

    This is safe to run at any time - it only affects the search index,
    not your actual notes or data.

    Args:
        project: Optional project name. If not provided, uses the default project.

    Returns:
        Confirmation message about the reindex operation

    Example:
        force_reindex()
        force_reindex(project="my-project")
    """
    track_mcp_tool("force_reindex")

    async with get_client() as client:
        if context:  # pragma: no cover
            await context.info("Starting full reindex of search index")

        # Get active project using the standard project resolution
        active_project = await get_active_project(client, project, context)
        
        logger.info(f"Triggering reindex for project: {active_project.name}")

        # Call the reindex API endpoint
        response = await client.post(
            f"/{active_project.permalink}/search/reindex",
        )

        if response.status_code != 200:
            error_detail = response.text
            return f"# Error\n\nFailed to trigger reindex: {error_detail}"

        result_data = response.json()

        result = "# Search Index Reindex\n\n"
        result += f"Project: {active_project.name}\n"
        result += f"Status: {result_data.get('status', 'unknown')}\n"
        result += f"Message: {result_data.get('message', 'Reindex initiated')}\n\n"
        result += "The search index is being rebuilt in the background.\n"
        result += "This may take a few moments for large vaults.\n\n"
        result += "You can verify the reindex by searching for content that was previously not found."

        return result
