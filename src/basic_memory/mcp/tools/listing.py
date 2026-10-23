"""bm_list: list one directory level.

`bm_list("/")` with no project lists the projects this session can address, so
listing doubles as project discovery; any other path lists one level of a project.
Recursive listing is bm_find's job.
"""

import os
from typing import Annotated, Any

from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.mcp.clients import DirectoryClient
from basic_memory.mcp.directory_listing import format_listing_text, project_mount_listing
from basic_memory.mcp.note_targets import open_project_target
from basic_memory.mcp.refusals import refusal
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
        "title, date and id. `bm_list('/')` with no project lists the projects you can "
        "address; '<project>/path' lists inside another project. Filter files with `glob` "
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
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """List one directory level, or the addressable projects at '/'."""
    # Trigger: the project root with no project named, by parameter or by the
    #          BASIC_MEMORY_MCP_PROJECT constraint.
    # Why: projects are mount points (#1415); listing them in-band makes discovery
    #      the same call as browsing.
    # Outcome: the projects as directory entries, each a routable '/<project>' path.
    if (
        project is None
        and not os.environ.get("BASIC_MEMORY_MCP_PROJECT")
        and not path.strip().strip("/")
    ):
        # The projects are folders, which `glob` and `sort` never apply to.
        if glob is not None or sort is not None:
            raise refusal(
                "INVALID_ARGUMENTS",
                "'/' lists projects; `glob` and `sort` apply to files in a folder.",
                try_instead="bm_list(path='<project>', glob=...), or bm_find to search by name",
                output_format=output_format,
            )
        mounts = await project_mount_listing(page=page, page_size=page_size, context=context)
        if output_format == "json":
            return mounts
        text = format_listing_text(
            DirectoryListResponse.model_validate(mounts),
            heading="Projects:",
            next_call=Pagination.of(mounts).next_call("bm_list", {"path": "/"}),
        )
        note = mounts.get("note")
        return f"{text}\n\nNote: {note}" if note else text

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
