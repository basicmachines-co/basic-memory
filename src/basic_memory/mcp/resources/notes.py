"""Notes as MCP resources.

Basic Memory hands out ``memory://`` URLs everywhere — pages, prompts, handoffs,
conversation summaries — so reading one through the standard MCP
``resources/read`` must work too. ``memory://{project}/{path*}`` returns the
note's raw markdown, exactly as it sits on disk, frontmatter included.
"""

from fastmcp import Context
from fastmcp.exceptions import ResourceError, ToolError

from basic_memory.mcp.project_context import get_project_client, resolve_project_and_path
from basic_memory.mcp.resources.man import manual_page
from basic_memory.mcp.resources.project_info import project_info
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get, resolve_entity_id

NOTE_TEMPLATE = "memory://{project}/{path*}"


async def read_note_markdown(identifier: str, context: Context | None) -> str:
    """Read one note's raw markdown by its memory:// identifier.

    Routing uses the same semantics as the tools (resolve_project_and_path): a
    leading segment that names a project routes there, and otherwise — legacy
    unprefixed permalinks, permalinks_include_project=False — the whole path is
    resolved in the active/default project.
    """
    try:
        async with get_project_client(None, context) as (client, active_project):
            target, entity_path, _ = await resolve_project_and_path(
                client, f"memory://{identifier}", active_project.name, context
            )
            entity_id = await resolve_entity_id(client, target.external_id, entity_path)
            response = await call_get(
                client, f"/v2/projects/{target.external_id}/resource/{entity_id}"
            )
    except (ValueError, RuntimeError) as error:
        # Routing failed before any read happened (a constrained or unresolvable
        # route, or the cloud workspace index consulted without credentials).
        raise ResourceError(str(error)) from error
    except ToolError as error:
        # call_get/call_post wrap every HTTP failure in ToolError; only a confirmed
        # not-found should read as a missing note — auth, server, and transport
        # failures keep their actionable cause.
        if "not found" in str(error).lower():
            raise ResourceError(
                f"No note {identifier!r}; search_notes can find the identifier"
            ) from error
        raise ResourceError(str(error)) from error

    content_type = response.headers.get("content-type", "")
    # Only text comes back byte-exact; steer binaries to the tool built for them.
    if not (content_type.startswith("text/") or content_type == "application/json"):
        raise ResourceError(
            f"{identifier!r} is {content_type or 'binary'}; use the read_content tool "
            "for non-text files"
        )
    return response.text


@mcp.resource(
    uri=NOTE_TEMPLATE,
    name="note",
    description=(
        "A note's raw markdown, addressed by its memory:// URL — "
        "memory://<project>/<identifier>, e.g. memory://research/specs/search-design. "
        "The identifier may be a permalink, a title, or a file path in the project."
    ),
    mime_type="text/markdown",
)
async def note_resource(project: str, path: str, context: Context | None = None) -> str:
    """Return the raw markdown of one note."""
    # `man` is the manual's namespace, not a project, and which template a server
    # matches first is not guaranteed — so behave identically to the manual either way.
    if project == "man":
        return manual_page(path)

    # The {workspace}/{project}/info shape belongs to the project_info resource,
    # which itself falls back to a note named .../info — delegating keeps both
    # handlers' answers identical whichever template wins the tie.
    head, _, tail = path.rpartition("/")
    if tail == "info" and head and "/" not in head:
        return await project_info(workspace=project, project=head, context=context)

    return await read_note_markdown(f"{project}/{path}", context)
