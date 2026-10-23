"""bm_find: locate files by name or frontmatter, recursively (SPEC-35.2, #1830).

Replaces find. Two arms, chosen by whether `filters` is given:

- without filters, a recursive directory listing, optionally narrowed by a
  file-name glob and bounded by depth;
- with filters, a frontmatter query scoped to the folder subtree, optionally
  projecting each hit down to chosen frontmatter `fields`.

The metadata search has no file-name glob and no depth bound, so `name` and a
non-default `depth` cannot combine with `filters`; that is a refusal naming the
alternative, never a silently ignored argument.
"""

from typing import Annotated, Any

from fastmcp import Context
from pydantic import BeforeValidator, Field

from basic_memory.mcp.clients import DirectoryClient
from basic_memory.mcp.directory_listing import format_listing_text
from basic_memory.mcp.metadata_find import find_by_metadata, parse_field_paths
from basic_memory.mcp.note_targets import open_project_target, qualify_listing_paths
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import (
    Pagination,
    DirectoryPath,
    Filters,
    OutputFormat,
    Page,
    PageSize,
    ProjectParam,
)
from basic_memory.schemas.directory import DirectoryListResponse
from basic_memory.utils import coerce_list

# The directory API bounds recursion at 10 levels.
MAX_FIND_DEPTH = 10


@mcp.tool(
    name="bm_find",
    title="Find files",
    description=(
        "Find files in a Basic Memory project, recursively. With `name`, match file names "
        "against a glob ('*meeting*'). With `filters`, match notes by frontmatter, e.g. "
        '{"status": "active"} or {"priority": {"$in": ["high"]}}, and with `fields` return '
        "just those frontmatter values per note. `path` limits the search to a folder. "
        "For ranked text or meaning search, use bm_search."
    ),
    tags={"navigation"},
    annotations={
        "title": "Find files",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_find(
    path: DirectoryPath = "/",
    name: Annotated[
        str | None, Field(description="File-name glob, e.g. '*.md'. Not with `filters`.")
    ] = None,
    filters: Filters = None,
    fields: Annotated[
        list[str] | None,
        BeforeValidator(coerce_list),
        Field(
            description=(
                "Frontmatter fields to return per note (dot paths allowed), e.g. "
                '["title", "priority"]. Needs `filters`.'
            )
        ),
    ] = None,
    depth: Annotated[
        int, Field(ge=1, le=MAX_FIND_DEPTH, description="Folder levels to search (1-10).")
    ] = MAX_FIND_DEPTH,
    page: Page = 1,
    page_size: PageSize = 10,
    output_format: OutputFormat = "text",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Find files by name, or notes by frontmatter."""
    # --- Argument combinations, before any I/O ---
    if filters is not None:
        if not filters:
            raise refusal(
                "INVALID_ARGUMENTS",
                "`filters` is empty.",
                try_instead="omit `filters` for a listing, or give at least one condition",
                output_format=output_format,
            )
        if name is not None:
            raise refusal(
                "INVALID_ARGUMENTS",
                "`name` cannot combine with `filters`: the frontmatter search has no file-name glob.",
                try_instead="limit the search with `path` instead",
                output_format=output_format,
            )
        if depth != MAX_FIND_DEPTH:
            raise refusal(
                "INVALID_ARGUMENTS",
                "`depth` cannot combine with `filters`: the frontmatter search covers the whole "
                "folder subtree.",
                try_instead="limit the search with `path` instead",
                output_format=output_format,
            )
    field_paths = None
    if fields is not None:
        if filters is None:
            raise refusal(
                "INVALID_ARGUMENTS",
                "`fields` needs `filters`; without filters bm_find lists files.",
                output_format=output_format,
            )
        try:
            field_paths = parse_field_paths(fields)
        except ValueError as error:
            raise refusal("INVALID_ARGUMENTS", str(error), output_format=output_format) from error

    async with open_project_target(path, project, context, output_format=output_format) as target:
        # --- Frontmatter arm ---
        if filters is not None:
            # "note_type" is the entity column's name for the frontmatter `type`;
            # search_notes and find accept both spellings, and so does bm_find.
            metadata_filters = {
                ("type" if key == "note_type" else key): value for key, value in filters.items()
            }
            hits = await find_by_metadata(
                target,
                metadata_filters=metadata_filters,
                fields=field_paths,
                page=page,
                page_size=page_size,
            )
            if output_format == "json":
                return hits
            next_call = Pagination.of(hits).next_call(
                "bm_find",
                {"path": path, "filters": filters, "fields": fields, "project": project},
            )
            return _format_hits(hits, path, next_call=next_call)

        # --- Listing arm ---
        list_path = f"/{target.route.path}" if target.route.stripped else path
        listing = await DirectoryClient(target.client, target.project.external_id).list(
            list_path,
            depth=depth,
            file_name_glob=name,
            page=page,
            page_size=page_size,
        )

    qualified = qualify_listing_paths(listing.model_dump(mode="json"), target.route)
    if output_format == "json":
        return qualified
    if listing.total == 0:
        matching = f" matching '{name}'" if name else ""
        return f"No files under '{path}'{matching}."

    matching = f" matching '{name}'" if name else ""
    next_call = Pagination.of(qualified).next_call(
        "bm_find",
        {
            "path": path,
            "name": name,
            # The default depth stays out of the call, as the caller left it out.
            "depth": depth if depth != MAX_FIND_DEPTH else None,
            "project": project,
        },
    )
    return format_listing_text(
        DirectoryListResponse.model_validate(qualified),
        heading=f"Files under '{path}'{matching}:",
        next_call=next_call,
    )


def _format_hits(hits: dict[str, Any], path: str, *, next_call: str | None) -> str:
    """One line per matching note: its path and title, then any projected fields."""
    results = hits["results"]
    if not results:
        return f"No notes under '{path}' match the filters."
    lines = [f"{hits.get('total', len(results))} notes under '{path}' match:", ""]
    for row in results:
        line = f"- {row['file_path']} | {row['title']}"
        projected = row.get("fields")
        if projected:
            line += " | " + ", ".join(f"{key}={value!r}" for key, value in projected.items())
        lines.append(line)
    if next_call is not None:
        lines.extend(["", f"More results available. Call {next_call} to continue."])
    return "\n".join(lines)
