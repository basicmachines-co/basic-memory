"""Project session management for Basic Memory MCP server.

Provides simple in-memory project context for MCP tools, allowing users to switch
between projects during a conversation without restarting the server.

Session Persistence Flow:
  1. Pre-tool: Middleware loads self.sessions[session_id] → sets context state
  2. Tool execution: Uses context.get_state("active_project")
  3. Project switching: Tool calls set_active_project() → updates context state
  4. Post-tool: Middleware detects context change → saves to self.sessions[session_id]
"""

from typing import Optional
from httpx import AsyncClient
from loguru import logger

from fastmcp import Context

from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.utils import generate_permalink


async def set_active_project(
    client: AsyncClient, *, context: Context | None, project: ProjectItem
) -> None:
    """Set the active project context.

    Args:
        client (AsyncClient): The client to use for making requests to fastapi.
        project_name: The project to switch to
        context: Optional FastMCP context containing project session state
    """
    active_project = await get_active_project(client, context=context)
    if active_project.name != project.name:
        previous_project = active_project
        # set project in the context
        if context:
            context.set_state("active_project", project)
            await context.info(
                f"Context {context.session_id} Switched active project: {previous_project} -> {project.name}"
            )
            logger.info(f"Switched active project: {previous_project} -> {project.name}")
    else:
        logger.debug(f"No change for active project: {active_project}")


async def get_active_project(
    client: AsyncClient, *, context: Context | None, project_override: Optional[str] = None
) -> ProjectItem:
    """
    Get the active project for a tool call.
    If no context is provided, the active project is returned from the default project

    Args:
        client (AsyncClient): The client to use for making requests to fastapi.
        project_override: Optional explicit project name from tool parameter
        context: Optional FastMCP context containing project session state

    Returns:
        The project to use for the tool call
    """

    # Try to get active project from context first
    if context:
        active_project = context.get_state("active_project")
        logger.debug(f"Context {context.session_id} found active project: {active_project}")
    else:
        response = await call_get(
            client,
            "/projects/default",
        )
        active_project = ProjectItem.model_validate(response.json())
        logger.debug(f"No context provided. Using default project: {active_project}")

    # Handle project override
    if project_override:
        logger.debug(f"overriding active project: {project_override}")
        permalink = generate_permalink(project_override)
        response = await call_get(
            client,
            f"/{permalink}/project/item",
        )
        active_project = ProjectItem.model_validate(response.json())

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
