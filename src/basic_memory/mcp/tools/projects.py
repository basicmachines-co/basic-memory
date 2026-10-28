"""bm_project and bm_project_delete: projects and workspaces.

bm_project lists projects (grouped by workspace), creates them, and shows what
changed recently. bm_project_delete removes one; it is its own tool so bm_project
is never destructive, and it is hidden unless the enable_project_delete setting
turns it on (see mcp.server.set_project_delete_visibility).

Each command runs the project tool implementation of the same purpose; these
tools give them one `command` parameter and the shared parameter types.
"""

from typing import Annotated, Any, Literal

from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import PROJECT_DELETE_TAG, mcp, set_project_delete_visibility
from basic_memory.mcp.tool_params import (
    Items,
    OutputFormat,
    Page,
    PageSize,
    OptionalProjectParam,
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

type ProjectCommand = Literal["list", "create", "recent"]

Workspace = Annotated[
    str | None,
    Field(description="In Cloud: the workspace slug, name or id that holds the project."),
]


@mcp.tool(
    name="bm_project",
    title="Projects",
    description=(
        "Basic Memory projects. `command`: list (projects, grouped by workspace); create "
        "(`name`, `path`, optional `set_default`, `workspace`); recent (what changed "
        "lately in the required `project`: `since`, `items`, paging)."
    ),
    tags={"projects"},
    annotations={
        "title": "Projects",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_project(
    command: Annotated[ProjectCommand, Field(description="What to do.")],
    name: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("name", "project_name"),
            description="For create: the new project's name.",
        ),
    ] = None,
    path: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("path", "project_path"),
            description="For create: the folder that holds the project's notes.",
        ),
    ] = None,
    set_default: Annotated[
        bool, Field(description="For create: make the new project the default.")
    ] = False,
    workspace: Workspace = None,
    since: Since = "7d",
    items: Items = None,
    page: Page = 1,
    page_size: PageSize = 10,
    output_format: OutputFormat = "text",
    project: OptionalProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any] | list[dict[str, Any]]:
    """List or create projects, or show what changed in them."""
    match command:
        case "list":
            return await list_memory_projects(output_format=output_format, context=context)
        case "create":
            if name is None or path is None:
                raise refusal(
                    "INVALID_ARGUMENTS",
                    "create needs `name` and `path`.",
                    try_instead="bm_project(command='create', name='research', path='~/research')",
                    output_format=output_format,
                )
            return await create_memory_project(
                project_name=name,
                project_path=path,
                set_default=set_default,
                workspace=workspace,
                output_format=output_format,
                context=context,
            )
        case "recent":
            if project is None:
                raise refusal(
                    "INVALID_ARGUMENTS",
                    "recent needs `project`, a project name, workspace/project or external id.",
                    try_instead="bm_project(command='list') to discover projects",
                    output_format=output_format,
                )
            project_name, project_id = routing_arguments(parse_project(project))
            activity = await recent_activity_page(
                # recent_activity names whole notes "entity", the API's word.
                type=["entity" if item == "note" else item for item in items] if items else "",
                depth=1,
                timeframe=since,
                page=page,
                page_size=page_size,
                project=project_name,
                project_id=project_id,
                output_format=output_format,
                context=context,
            )
            # Text is returned as rendered. JSON has one shape, as bm_search has: rows
            # with paging, or no rows plus the reason the miss is not final (a project
            # that was never indexed).
            match activity:
                case str() as text if output_format == "text":
                    return text
                case RecentRows() as recent:
                    payload: dict[str, Any] = {
                        "results": recent.rows,
                        "page": recent.page,
                        "page_size": recent.page_size,
                        "has_more": recent.has_more,
                    }
                    # Projects never indexed, beside the activity of those that were.
                    if recent.guidance:
                        payload["guidance"] = "\n\n".join(recent.guidance)
                    return payload
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
            description="The project to delete, as bm_project(command='list') names it.",
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
