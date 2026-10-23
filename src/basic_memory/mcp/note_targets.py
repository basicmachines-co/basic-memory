"""Route a bm_* tool's target into its project and resolve notes exactly (#1829).

Every bm_* tool starts the same way: classify the `project` argument, let a
'<project>/path' prefix on the target choose or confirm the project, and open a
client routed to it. This module is that one path. It reuses the POSIX tools'
routing (`resolve_project_path_route`), so a bm_* tool accepts exactly the
project-qualified paths other tools return.

Resolution is exact. A target that names no note is a NOT_FOUND refusal that
points to `bm_search`; there is no fuzzy fallback, because a tool that edits or
deletes must never act on a near match.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastmcp import Context
from fastmcp.exceptions import ToolError
from httpx import AsyncClient, HTTPStatusError

from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.project_context import (
    ProjectPathRoute,
    get_project_client,
    resolve_project_path_route,
)
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.tool_params import OutputFormat, parse_project, routing_arguments
from basic_memory.schemas.project_info import ProjectItem


@dataclass(frozen=True, slots=True)
class ProjectTarget:
    """A target routed into its project, with a client for that project.

    `route.path` is the project-relative target: the input with any project prefix
    removed, "" when the input named the project itself.
    """

    client: AsyncClient
    project: ProjectItem
    route: ProjectPathRoute


@asynccontextmanager
async def open_project_target(
    target: str,
    project: str | None,
    context: Context | None,
) -> AsyncIterator[ProjectTarget]:
    """Route `target` and yield a client for the project it lives in."""
    project_name, project_id = routing_arguments(parse_project(project))
    route = await resolve_project_path_route(
        target, project=project_name, project_id=project_id, context=context
    )
    async with get_project_client(route.project, context=context, project_id=route.project_id) as (
        client,
        active_project,
    ):
        yield ProjectTarget(client=client, project=active_project, route=route)


def _is_not_found(error: ToolError) -> bool:
    """True when a typed-client failure wraps an HTTP 404 from the API."""
    cause = error.__cause__
    return isinstance(cause, HTTPStatusError) and cause.response.status_code == 404


async def resolve_note(
    target: ProjectTarget,
    identifier: str,
    *,
    output_format: OutputFormat = "text",
) -> str:
    """Resolve the routed target to a note's external id, exactly.

    `identifier` is the caller's original spelling, used in the refusal so the
    model sees what it asked for.
    """
    # Trigger: the input was a bare project name ('research' or 'research/').
    # Why: routing consumed the whole identifier; there is no note left to resolve.
    # Outcome: refuse with the call that lists the project instead.
    if target.route.stripped and not target.route.path:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"'{identifier}' names a project, not a note.",
            try_instead=f"bm_list(path='{identifier.rstrip('/')}/')",
            output_format=output_format,
        )

    knowledge_client = KnowledgeClient(target.client, target.project.external_id)
    try:
        return await knowledge_client.resolve_entity(target.route.path, strict=True)
    except ToolError as error:
        if not _is_not_found(error):
            raise
        raise refusal(
            "NOT_FOUND",
            f"No note matches '{identifier}' in project '{target.project.name}'.",
            try_instead=f"bm_search(query='{identifier}')",
            output_format=output_format,
        ) from error
