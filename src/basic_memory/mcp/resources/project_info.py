"""Project info tool for Basic Memory MCP server."""

from typing import Optional

from loguru import logger
from fastmcp import Context

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

    This tool provides detailed statistics and status information about your
    Basic Memory project, including:

    - Project configuration
    - Entity, observation, and relation counts
    - Graph metrics (most connected entities, isolated entities)
    - Recent activity and growth over time
    - System status (database, watch service, version)

    Use this tool to:
    - Verify your Basic Memory installation is working correctly
    - Get insights into your knowledge base structure
    - Monitor growth and activity over time
    - Identify potential issues like unresolved relations

    Args:
        project: Optional project name. If not provided, uses default_project
                (if default_project_mode=true) or CLI constraint. If unknown,
                use list_memory_projects() to discover available projects.
        context: Optional FastMCP context for performance caching.

    Returns:
        Detailed project information and statistics

    Examples:
        # Get information about the current/default project
        info = await project_info()

        # Get information about a specific project
        info = await project_info(project="my-project")

        # Check entity counts
        print(f"Total entities: {info.statistics.total_entities}")

        # Check system status
        print(f"Basic Memory version: {info.system.version}")
    """
    logger.info("Getting project info")

    async with get_client() as client:
        project_config = await get_active_project(client, project, context)
        project_url = project_config.permalink

        # Call the API endpoint
        response = await call_get(client, f"{project_url}/project/info")

        # Convert response to ProjectInfoResponse
        return ProjectInfoResponse.model_validate(response.json())
