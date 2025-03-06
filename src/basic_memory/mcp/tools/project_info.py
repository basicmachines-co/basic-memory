"""Project info tool for Basic Memory MCP server."""

import logfire
from loguru import logger

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas import ProjectInfoResponse
from basic_memory.mcp.async_client import client


@mcp.tool(
    description="Get information and statistics about the current Basic Memory project.",
)
@logfire.instrument(extract_args=False)
async def project_info() -> ProjectInfoResponse:
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

    Returns:
        Detailed project information and statistics

    Examples:
        # Get information about the current project
        info = await project_info()

        # Check entity counts
        print(f"Total entities: {info.statistics.total_entities}")

        # Check system status
        print(f"Basic Memory version: {info.system.version}")
    """
    logger.info("Getting project info")

    # Call the API endpoint
    response = await call_get(client, "/stats/project-info")

    # Convert response to ProjectInfoResponse
    return ProjectInfoResponse.model_validate(response.json())
