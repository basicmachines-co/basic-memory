"""bm_project: projects and workspaces (SPEC-35.2, #1832).

Replaces list_memory_projects (which already groups projects by workspace),
create_memory_project, recent_activity and, when enabled, delete_project. Each
command delegates to the implementation of the tool it replaces; this module only
gives them one name, one `command` parameter and the shared parameter types.

The tool is registered twice under one name: `core` without the delete command and
`with-delete` with it. The composition root shows one of the two from the
enable_project_delete setting (see mcp.server.set_project_delete_visibility).
"""

import os
from typing import Annotated, Any, Literal

from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp, set_project_delete_visibility
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
from basic_memory.mcp.tools.recent_activity import recent_activity

type ProjectCommand = Literal["list", "create", "recent"]
type ProjectCommandWithDelete = Literal["list", "create", "recent", "delete"]

_DESCRIPTION = (
    "Basic Memory projects. `command`: list (projects, grouped by workspace); create "
    "(`name`, `path`, optional `set_default`, `workspace`); recent (what changed lately: "
    "`since`, `items`, paging; omit `project` to see every project)."
)

# --- Parameters ---

NewProjectName = Annotated[
    str | None,
    Field(
        min_length=1,
        validation_alias=AliasChoices("name", "project_name"),
        description="For create and delete: the project's name.",
    ),
]
NewProjectPath = Annotated[
    str | None,
    Field(
        min_length=1,
        validation_alias=AliasChoices("path", "project_path"),
        description="For create: the folder that holds the project's notes.",
    ),
]
SetDefault = Annotated[bool, Field(description="For create: make the new project the default.")]
Workspace = Annotated[
    str | None,
    Field(description="For create and delete in Cloud: the workspace slug, name or id."),
]


@mcp.tool(
    name="bm_project",
    version="core",
    title="Projects",
    description=_DESCRIPTION,
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
    name: NewProjectName = None,
    path: NewProjectPath = None,
    set_default: SetDefault = False,
    workspace: Workspace = None,
    since: Since = "7d",
    items: Items = None,
    page: Page = 1,
    page_size: PageSize = 10,
    output_format: OutputFormat = "text",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any] | list[dict[str, Any]]:
    """List, create, or show recent activity in projects."""
    return await _run_project_command(
        command,
        name=name,
        path=path,
        set_default=set_default,
        workspace=workspace,
        delete_notes=False,
        since=since,
        items=items,
        page=page,
        page_size=page_size,
        output_format=output_format,
        project=project,
        context=context,
    )


@mcp.tool(
    name="bm_project",
    version="with-delete",
    title="Projects",
    description=(
        _DESCRIPTION + " delete (`name`, `delete_notes`, `workspace`) removes a project; with "
        "delete_notes=true its files go too. This cannot be undone."
    ),
    tags={"projects"},
    annotations={
        "title": "Projects",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_project_with_delete(
    command: Annotated[ProjectCommandWithDelete, Field(description="What to do.")],
    name: NewProjectName = None,
    path: NewProjectPath = None,
    set_default: SetDefault = False,
    workspace: Workspace = None,
    delete_notes: Annotated[
        bool, Field(description="For delete: also delete the project's files.")
    ] = False,
    since: Since = "7d",
    items: Items = None,
    page: Page = 1,
    page_size: PageSize = 10,
    output_format: OutputFormat = "text",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any] | list[dict[str, Any]]:
    """List, create, delete, or show recent activity in projects."""
    return await _run_project_command(
        command,
        name=name,
        path=path,
        set_default=set_default,
        workspace=workspace,
        delete_notes=delete_notes,
        since=since,
        items=items,
        page=page,
        page_size=page_size,
        output_format=output_format,
        project=project,
        context=context,
    )


# Hidden until the composition root reads enable_project_delete at startup.
set_project_delete_visibility(mcp, False)


async def _run_project_command(
    command: ProjectCommandWithDelete,
    *,
    name: str | None,
    path: str | None,
    set_default: bool,
    workspace: str | None,
    delete_notes: bool,
    since: str,
    items: list[Literal["note", "observation", "relation"]] | None,
    page: int,
    page_size: int,
    output_format: OutputFormat,
    project: str | None,
    context: Context | None,
) -> str | dict[str, Any] | list[dict[str, Any]]:
    """Check the arguments a command takes, then run the tool it replaces."""
    # Trigger: the server is constrained to one project (BASIC_MEMORY_MCP_PROJECT).
    # Why: such a session serves that project only; adding or removing projects
    #      belongs to whoever configured it. The replaced tools answered this
    #      with a success payload describing the failure (C11).
    # Outcome: an UNSUPPORTED refusal that names the CLI command instead.
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project and command in ("create", "delete"):
        cli = "add" if command == "create" else "remove"
        raise refusal(
            "UNSUPPORTED",
            f"This MCP server is constrained to project '{constrained_project}', so it "
            f"cannot {command} projects.",
            try_instead=f"the CLI: basic-memory project {cli} ...",
            output_format=output_format,
        )
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
            project_name, project_id = routing_arguments(parse_project(project))
            return await recent_activity(
                # recent_activity names whole notes "entity", the API's word.
                type=["entity" if item == "note" else item for item in items] if items else "",
                timeframe=since,
                page=page,
                page_size=page_size,
                project=project_name,
                project_id=project_id,
                output_format=output_format,
                context=context,
            )
        case "delete":
            if name is None:
                raise refusal(
                    "INVALID_ARGUMENTS",
                    "delete needs `name`, the project to delete.",
                    try_instead="bm_project(command='list') to see project names",
                    output_format=output_format,
                )
            return await delete_project(
                project_name=name,
                delete_notes=delete_notes,
                workspace=workspace,
                context=context,
            )
