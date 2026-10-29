"""bm_list: list one directory level.

Every listing names its project explicitly. Project discovery is bm_project's job;
recursive listing is bm_find's job.
"""

from typing import Annotated, Any

from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.mcp.clients import DirectoryClient
from basic_memory.mcp.directory_listing import format_listing_text
from basic_memory.mcp.note_targets import open_project_target
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import (
    Pagination,
    DirectoryPath,
    OutputFormat,
    Page,
    PageSize,
    ProjectParam,
)
from basic_memory.schemas.directory import DirectoryListResponse, DirectorySortOrder


@mcp.tool(
    name="bm_list",
    title="List folder",
    description=(
        "List one folder of a Basic Memory project: its subfolders, then its files with "
        "title, date and id. `project` is required; '/' lists its root. Discover projects "
        "with bm_project(command='list'). Filter files with `glob` "
        "and order them with `sort`. For a recursive search by name or frontmatter, use "
        "bm_find."
    ),
    tags={"navigation", "notes"},
    annotations={
        "title": "List folder",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_list(
    path: DirectoryPath = "/",
    glob: Annotated[
        str | None,
        Field(
            validation_alias=AliasChoices("glob", "file_name_glob", "pattern"),
            description="File-name glob, e.g. '*.md' or '*meeting*'.",
        ),
    ] = None,
    sort: Annotated[
        DirectorySortOrder | None,
        Field(description="File order. Folders always come first."),
    ] = None,
    page: Page = 1,
    page_size: PageSize = 10,
    output_format: OutputFormat = "text",
    *,
    project: ProjectParam,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """List one directory level within the required project."""
    async with open_project_target(path, project, context, output_format=output_format) as target:
        list_path = f"/{target.route.path}" if target.route.stripped else path
        listing = await DirectoryClient(target.client, target.project.external_id).list(
            list_path,
            depth=1,
            file_name_glob=glob,
            sort=sort,
            page=page,
            page_size=page_size,
        )

    # Paths come back in the spelling that was used to reach them, so a listed
    # entry can be passed straight to bm_read or bm_list.
    qualified = target.paths.listing(listing.model_dump(mode="json"))
    if output_format == "json":
        return qualified

    if listing.total == 0:
        matching = f" matching '{glob}'" if glob else ""
        return f"No files in '{path}'{matching}."

    matching = f" matching '{glob}'" if glob else ""
    return format_listing_text(
        DirectoryListResponse.model_validate(qualified),
        heading=f"Contents of '{path}'{matching}:",
        next_call=Pagination.of(qualified).next_call(
            "bm_list", {"path": path, "glob": glob, "sort": sort, "project": project}
        ),
    )
