"""Project context utilities for Basic Memory MCP server.

Provides project lookup utilities for MCP tools.
Handles project validation and context management in one place.
"""

import os
from typing import Optional, List
from httpx import AsyncClient
from httpx._types import (
    HeaderTypes,
)
from loguru import logger
from fastmcp import Context

from basic_memory.config import ConfigManager
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.utils import generate_permalink


async def resolve_project_parameter(project: Optional[str] = None) -> Optional[str]:
    """Resolve project parameter using three-tier hierarchy.

    if config.cloud_mode:
        project is required
    else:
        Resolution order:
        1. Single Project Mode  (--project cli arg, or BASIC_MEMORY_MCP_PROJECT env var) - highest priority
        2. Explicit project parameter - medium priority
        3. Default project if default_project_mode=true - lowest priority

    Args:
        project: Optional explicit project parameter

    Returns:
        Resolved project name or None if no resolution possible
    """

    config = ConfigManager().config
    # if cloud_mode, project is required
    if config.cloud_mode:
        if project:
            logger.debug(f"project: {project}, cloud_mode: {config.cloud_mode}")
            return project
        else:
            raise ValueError("No project specified. Project is required for cloud mode.")

    # Priority 1: CLI constraint overrides everything (--project arg sets env var)
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project:
        logger.debug(f"Using CLI constrained project: {constrained_project}")
        return constrained_project

    # Priority 2: Explicit project parameter
    if project:
        logger.debug(f"Using explicit project parameter: {project}")
        return project

    # Priority 3: Default project mode
    if config.default_project_mode:
        logger.debug(f"Using default project from config: {config.default_project}")
        return config.default_project

    # No resolution possible
    return None


async def get_project_names(client: AsyncClient, headers: HeaderTypes | None = None) -> List[str]:
    response = await call_get(client, "/projects/projects", headers=headers)
    project_list = ProjectList.model_validate(response.json())
    return [project.name for project in project_list.projects]


async def get_active_project(
    client: AsyncClient,
    project: Optional[str] = None,
    context: Optional[Context] = None,
    headers: HeaderTypes | None = None,
) -> ProjectItem:
    """Get and validate project, setting it in context if available.

    Args:
        client: HTTP client for API calls
        project: Optional project name (resolved using hierarchy)
        context: Optional FastMCP context to cache the result

    Returns:
        The validated project item

    Raises:
        ValueError: If no project can be resolved
        HTTPError: If project doesn't exist or is inaccessible
    """
    resolved_project = await resolve_project_parameter(project)
    if not resolved_project:
        project_names = await get_project_names(client, headers)
        raise ValueError(
            "No project specified. "
            "Either set 'default_project_mode=true' in config, or use 'project' argument.\n"
            f"Available projects: {project_names}"
        )

    project = resolved_project

    # Check if already cached in context
    if context:
        cached_project = context.get_state("active_project")
        if cached_project and cached_project.name == project:
            logger.debug(f"Using cached project from context: {project}")
            return cached_project

    # Validate project exists by calling API
    logger.debug(f"Validating project: {project}")
    permalink = generate_permalink(project)
    response = await call_get(client, f"/{permalink}/project/item", headers=headers)
    active_project = ProjectItem.model_validate(response.json())

    # Cache in context if available
    if context:
        context.set_state("active_project", active_project)
        logger.debug(f"Cached project in context: {project}")

    logger.debug(f"Validated project: {active_project.name}")
    return active_project


def add_project_metadata(result: str, project_name: str) -> str:
    """Add project context as metadata footer for assistant session tracking.

    Provides clear project context to help the assistant remember which
    project is being used throughout the conversation session.

    Args:
        result: The tool result string
        project_name: The project name that was used

    Returns:
        Result with project session tracking metadata
    """
    return f"{result}\n\n[Session: Using project '{project_name}']"
