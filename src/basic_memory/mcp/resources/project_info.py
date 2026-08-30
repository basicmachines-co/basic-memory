"""Project info resource for Basic Memory MCP server."""

from fastmcp import Context
from fastmcp.exceptions import ResourceError
from loguru import logger

from basic_memory.config import ConfigManager, ProjectMode
from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.project_context_identifiers import canonicalize_project_name
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas import ProjectInfoResponse


@mcp.resource(
    uri="memory://{workspace}/{project}/info",
    description="Get information and statistics about a Basic Memory workspace project.",
)
async def project_info(
    workspace: str,
    project: str,
    context: Context | None = None,
) -> str:
    """Get comprehensive information about a workspace-qualified Basic Memory project.

    This resource provides detailed statistics and status information about a
    Basic Memory project, including:

    - Project configuration
    - Entity, observation, and relation counts
    - Graph metrics (most connected entities, isolated entities)
    - Recent activity and growth over time
    - System status (database, watch service, version)

    Args:
        workspace: Workspace permalink from the resource URI. Use ``local`` for
            a configured local project.
        project: Project permalink from the resource URI.
        context: Optional FastMCP context for performance caching.

    Returns:
        Detailed project information and statistics as a JSON document —
        resources carry text, so the validated response is serialized here.
    """
    logger.info("Getting project info")

    project_route = f"{workspace}/{project}"
    config = ConfigManager().config
    configured_project = canonicalize_project_name(project, config)

    # Trigger: the canonical resource URI uses the `local` workspace sentinel.
    # Why: local projects have no workspace route, but every project-info URI now
    #   has the same workspace/project shape.
    # Outcome: remove only that sentinel before the shared router resolves the project.
    if (
        workspace == "local"
        and configured_project is not None
        and config.get_project_mode(configured_project) is ProjectMode.LOCAL
    ):
        project_route = configured_project

    try:
        async with get_project_client(project_route, context) as (client, active_project):
            response = await call_get(client, f"/v2/projects/{active_project.external_id}/info")
            try:
                info = ProjectInfoResponse.model_validate(response.json())
            except ValueError as payload_error:
                # A reachable route answered with an incompatible payload — a backend
                # fault to surface, never a cue for the outer handler to serve a note.
                raise ResourceError(
                    f"Project info for '{project_route}' returned an invalid payload: "
                    f"{payload_error}"
                ) from payload_error
            return info.model_dump_json(indent=2)
    except (ValueError, RuntimeError) as error:
        # This template also wins ties for {project}/{directory}/info note URIs
        # (precedence between overlapping template matches is undefined), so a
        # failed workspace/project route may really be a note whose canonical
        # permalink ends in /info. Deferred import: notes.py imports this module.
        from basic_memory.mcp.resources.notes import NoteNotFoundError, read_note_markdown

        try:
            return await read_note_markdown(f"{workspace}/{project}/info", context)
        except NoteNotFoundError:
            # Neither a project route nor a note — the route error is the cause.
            # An operational note failure (auth, server, transport) propagates
            # with its own cause instead.
            raise ResourceError(str(error)) from error
