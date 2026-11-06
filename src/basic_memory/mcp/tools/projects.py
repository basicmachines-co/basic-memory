"""Project tools: list, create, recent changes, delete.

One tool per operation, so each carries its own annotations and required
parameters (#2030). bm_project_delete is hidden unless the enable_project_delete
setting turns it on (see mcp.server.set_project_delete_visibility).
"""

from typing import Annotated, Any

from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.mcp.server import PROJECT_DELETE_TAG, mcp, set_project_delete_visibility
from basic_memory.mcp.tool_params import (
    Items,
    OutputFormat,
    Page,
    PageSize,
    ProjectParam,
    Since,
    parse_project,
    routing_arguments,
)
from basic_memory.mcp.tools.project_management import (
    create_memory_project,
    delete_project,
    list_memory_projects,
)
from basic_memory.mcp.tools.recent_activity import RecentRows, recent_activity_page

Workspace = Annotated[
    str | None,
    Field(description="In Cloud: the workspace slug, name or id that holds the project."),
]


@mcp.tool(
    name="bm_project_list",
    title="List projects",
    description="List the Basic Memory projects you can reach, grouped by workspace.",
    tags={"projects"},
    annotations={
        "title": "List projects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_project_list(
    output_format: OutputFormat = "text",
    context: Context | None = None,
) -> str | dict[str, Any]:
    """List projects."""
    return await list_memory_projects(output_format=output_format, context=context)


@mcp.tool(
    name="bm_project_create",
    title="Create project",
    description=(
        "Create a Basic Memory project named `name` whose notes live in the folder `path`. "
        "In Cloud, `workspace` picks the workspace that holds it."
    ),
    tags={"projects"},
    annotations={
        "title": "Create project",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_project_create(
    name: Annotated[
        str,
        Field(
            min_length=1,
            validation_alias=AliasChoices("name", "project_name"),
            description="The new project's name.",
        ),
    ],
    path: Annotated[
        str,
        Field(
            min_length=1,
            validation_alias=AliasChoices("path", "project_path"),
            description="The folder that holds the project's notes.",
        ),
    ],
    set_default: Annotated[bool, Field(description="Make the new project the default.")] = False,
    workspace: Workspace = None,
    output_format: OutputFormat = "text",
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Create one project."""
    return await create_memory_project(
        project_name=name,
        project_path=path,
        set_default=set_default,
        workspace=workspace,
        output_format=output_format,
        context=context,
    )


@mcp.tool(
    name="bm_recent",
    title="Recent changes",
    description=(
        "What changed lately in a Basic Memory project: notes, observations or relations "
        "modified within `since`, newest first, paged."
    ),
    tags={"projects"},
    annotations={
        "title": "Recent changes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_recent(
    project: ProjectParam,
    since: Since = "7d",
    items: Items = None,
    page: Page = 1,
    page_size: PageSize = 10,
    output_format: OutputFormat = "text",
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Show what changed in one project."""
    project_name, project_id = routing_arguments(parse_project(project))
    activity = await recent_activity_page(
        items=items,
        timeframe=since,
        page=page,
        page_size=page_size,
        project=project_name,
        project_id=project_id,
        output_format=output_format,
        context=context,
    )
    # Text is returned as rendered. JSON has one shape, as bm_search has: rows with
    # paging, or no rows plus the reason the miss is not final (a project that was never
    # indexed).
    match activity:
        case str() as text if output_format == "text":
            return text
        case RecentRows() as recent:
            return {
                "results": recent.rows,
                "page": recent.page,
                "page_size": recent.page_size,
                "has_more": recent.has_more,
            }
        case str() as guidance:
            return {"results": [], "guidance": guidance}


@mcp.tool(
    name="bm_project_delete",
    title="Delete project",
    description=(
        "Remove a Basic Memory project. Its files stay on disk unless `delete_notes=true`; "
        "a Cloud project's files are always deleted. This cannot be undone."
    ),
    tags={"projects", PROJECT_DELETE_TAG},
    annotations={
        "title": "Delete project",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_project_delete(
    name: Annotated[
        str,
        Field(
            min_length=1,
            validation_alias=AliasChoices("name", "project_name"),
            description="The project to delete, as bm_project_list names it.",
        ),
    ],
    delete_notes: Annotated[
        bool, Field(description="Also delete the project's note files.")
    ] = False,
    workspace: Workspace = None,
    output_format: OutputFormat = "text",
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Delete one project."""
    return await delete_project(
        project_name=name,
        delete_notes=delete_notes,
        workspace=workspace,
        output_format=output_format,
        context=context,
    )


# Hidden until the composition root reads enable_project_delete at startup.
set_project_delete_visibility(mcp, False)
