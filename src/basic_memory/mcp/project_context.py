"""Project context utilities for Basic Memory MCP server.

Provides project lookup utilities for MCP tools.
Handles project validation and context management in one place.
"""

import os
from typing import Optional
from httpx import AsyncClient
from loguru import logger
from fastmcp import Context

from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.utils import generate_permalink


async def get_active_project(
    client: AsyncClient, project: str, context: Optional[Context] = None
) -> ProjectItem:
    """Get and validate project, setting it in context if available.

    Args:
        client: HTTP client for API calls
        project: Required project name (may be overridden by server constraint)
        context: Optional FastMCP context to cache the result

    Returns:
        The validated project item

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
    """
    # Check for project constraint from MCP server
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project:
        if project != constrained_project:
            logger.debug(
                f"Overriding project '{project}' with constrained project '{constrained_project}'"
            )
        project = constrained_project

    # Check if already cached in context
    if context:
        cached_project = context.get_state("active_project")
        if cached_project and cached_project.name == project:
            logger.debug(f"Using cached project from context: {project}")
            return cached_project

    # Validate project exists by calling API
    logger.debug(f"Validating project: {project}")
    permalink = generate_permalink(project)
    response = await call_get(client, f"/{permalink}/project/item")
    active_project = ProjectItem.model_validate(response.json())

    # Cache in context if available
    if context:
        context.set_state("active_project", active_project)
        logger.debug(f"Cached project in context: {project}")

    logger.debug(f"Validated project: {active_project.name}")
    return active_project


def add_project_metadata(result: str, project_name: str) -> str:
    """Add project context as metadata footer for LLM awareness.

    Args:
        result: The tool result string
        project_name: The project name that was used

    Returns:
        Result with project metadata footer
    """
    return f"{result}\n\n<!-- Project: {project_name} -->"  # pragma: no cover
