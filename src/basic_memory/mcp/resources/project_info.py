"""Project info resource for Basic Memory MCP server."""

from typing import Optional

from fastmcp import Context
from loguru import logger

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.project_context import get_active_project
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas import ProjectInfoResponse


@mcp.resource(
    uri="memory://{project}/info",
    description="Get information and statistics about the current Basic Memory project.",
)
async def project_info(
    project: Optional[str] = None, context: Context | None = None
) -> ProjectInfoResponse:
    """Get comprehensive information about the current Basic Memory project.

    This resource provides detailed statistics and status information about a
    Basic Memory project, including:

    - Project configuration
    - Entity, observation, and relation counts
    - Graph metrics (most connected entities, isolated entities)
    - Recent activity and growth over time
    - System status (database, watch service, version)

    Args:
        project: Optional project name. If not provided, uses default_project
            from config or CLI constraint.
        context: Optional FastMCP context for performance caching.

    Returns:
        Detailed project information and statistics.
    """
    logger.info("Getting project info")

    async with get_client() as client:
        project_config = await get_active_project(client, project, context)
        response = await call_get(client, f"/v2/projects/{project_config.external_id}/info")
        return ProjectInfoResponse.model_validate(response.json())
