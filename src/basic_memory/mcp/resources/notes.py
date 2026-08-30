"""Notes as MCP resources.

Basic Memory hands out ``memory://`` URLs everywhere — pages, prompts, handoffs,
conversation summaries — so reading one through the standard MCP
``resources/read`` must work too. ``memory://{project}/{path*}`` returns the
note's raw markdown, exactly as it sits on disk, frontmatter included.
"""

from fastmcp import Context
from fastmcp.exceptions import ResourceError, ToolError

from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.resources.man import manual_page
from basic_memory.mcp.resources.project_info import project_info
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get, resolve_entity_id

NOTE_TEMPLATE = "memory://{project}/{path*}"


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
    # but precedence between overlapping template matches is not guaranteed and
    # this template can win the tie. Serve info URIs there first; a genuine note
    # whose path ends in /info is still read when no such workspace project exists.
    head, _, tail = path.rpartition("/")
    if tail == "info" and head and "/" not in head:
        try:
            return await project_info(workspace=project, project=head, context=context)
        except (ValueError, RuntimeError, ToolError):
            # Not a workspace/project pair — fall through to the note lookup.
            pass

    try:
        async with get_project_client(project, context) as (client, active_project):
            entity_id = await resolve_entity_id(client, active_project.external_id, path)
            response = await call_get(
                client, f"/v2/projects/{active_project.external_id}/resource/{entity_id}"
            )
    except (ValueError, RuntimeError) as error:
        # Project resolution failed before any read happened: ValueError for an
        # unresolvable route, RuntimeError when the unknown-name fallback consults
        # the cloud workspace index without credentials. Both are user-addressable.
        raise ResourceError(str(error)) from error
    except ToolError as error:
        raise ResourceError(
            f"No note {path!r} in project {project!r}; search_notes can find the identifier"
        ) from error

    content_type = response.headers.get("content-type", "")
    # Only text comes back byte-exact; steer binaries to the tool built for them.
    if not (content_type.startswith("text/") or content_type == "application/json"):
        raise ResourceError(
            f"{path!r} is {content_type or 'binary'}; use the read_content tool for non-text files"
        )
    return response.text
