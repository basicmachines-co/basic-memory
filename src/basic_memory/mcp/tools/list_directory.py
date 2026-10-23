"""List directory tool for Basic Memory MCP server."""

from typing import Annotated, Any, Literal, Optional

from loguru import logger
from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.mcp.directory_listing import format_listing_text
from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import Pagination
from basic_memory.schemas.directory import (
    DEFAULT_DIRECTORY_PAGE_SIZE,
    MAX_DIRECTORY_PAGE_SIZE,
    DirectorySortOrder,
)


@mcp.tool(
    title="List Directory",
    description="List directory contents with filtering and depth control.",
    tags={"navigation", "notes"},
    annotations={
        "title": "List Directory",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def list_directory(
    # `dir_name` is unusual; models reach for directory/folder/path/dir.
    dir_name: Annotated[
        str,
        Field(
            default="/",
            validation_alias=AliasChoices("dir_name", "directory", "folder", "path", "dir"),
        ),
    ] = "/",
    depth: int = 1,
    file_name_glob: Annotated[
        Optional[str],
        Field(
            default=None,
            validation_alias=AliasChoices("file_name_glob", "glob", "pattern", "filter"),
        ),
    ] = None,
    sort: DirectorySortOrder | None = None,
    page: int = 1,
    page_size: Annotated[
        int,
        Field(
            default=DEFAULT_DIRECTORY_PAGE_SIZE,
            validation_alias=AliasChoices("page_size", "limit", "per_page"),
        ),
    ] = DEFAULT_DIRECTORY_PAGE_SIZE,
    output_format: Literal["text", "json"] = "text",
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """List directory contents from the knowledge base with optional filtering.

    This tool provides 'ls' functionality for browsing the knowledge base directory structure.
    It can list immediate children or recursively explore subdirectories with depth control,
    and supports glob pattern filtering for finding specific files.

    Args:
        dir_name: Directory path to list (default: root "/")
                 Examples: "/", "/projects", "/research/ml"
        depth: Recursion depth (1-10, default: 1 for immediate children only)
               Higher values show subdirectory contents recursively
        file_name_glob: Optional glob pattern for filtering file names
                       Examples: "*.md", "*meeting*", "project_*"
        sort: Optional file ordering: "title_asc", "title_desc", "updated_asc",
              or "updated_desc". Directories remain first.
        page: One-indexed result page (default: 1)
        page_size: Number of nodes per page (default: 10, maximum: 200)
        output_format: "text" for a readable listing or "json" for structured pagination data
        project: Project name to list directory from. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from list_memory_projects().
        context: Optional FastMCP context for performance caching.

    Returns:
        Formatted listing of directory contents with file metadata

    Examples:
        # List root directory contents
        list_directory()

        # List specific folder
        list_directory(dir_name="/projects")

        # Find all markdown files
        list_directory(file_name_glob="*.md")

        # Deep exploration of research folder
        list_directory(dir_name="/research", depth=3)

        # Find meeting notes in projects folder
        list_directory(dir_name="/projects", file_name_glob="*meeting*")

        # Continue a large listing
        list_directory(dir_name="/projects", page=2, page_size=10)

        # List folders first, then notes from newest to oldest
        list_directory(dir_name="/projects", sort="updated_desc")

        # Explicit project specification
        list_directory(project="work-docs", dir_name="/projects")

    Raises:
        ToolError: If project doesn't exist or directory path is invalid
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")
    if page_size > MAX_DIRECTORY_PAGE_SIZE:
        raise ValueError(f"page_size must be <= {MAX_DIRECTORY_PAGE_SIZE}, got {page_size}")

    async with get_project_client(project, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        logger.debug(
            f"Listing directory '{dir_name}' in project {project} with "
            f"depth={depth}, glob='{file_name_glob}', sort={sort}"
        )

        # Import here to avoid circular import
        from basic_memory.mcp.clients import DirectoryClient

        # Use typed DirectoryClient for API calls
        directory_client = DirectoryClient(client, active_project.external_id)
        listing = await directory_client.list(
            dir_name,
            depth=depth,
            file_name_glob=file_name_glob,
            sort=sort,
            page=page,
            page_size=page_size,
        )

        if output_format == "json":
            return listing.model_dump(mode="json")

        if listing.total == 0:
            filter_desc = f" matching '{file_name_glob}'" if file_name_glob else ""
            return f"No files found in directory '{dir_name}'{filter_desc}"

        heading = (
            f"Files in '{dir_name}' matching '{file_name_glob}' (depth {depth}):"
            if file_name_glob
            else f"Contents of '{dir_name}' (depth {depth}):"
        )
        next_call = Pagination.of(listing.model_dump()).next_call(
            "list_directory",
            {
                "dir_name": dir_name,
                "depth": depth,
                "file_name_glob": file_name_glob,
                "sort": sort,
                # project_id routes exactly, so it wins when both were given.
                "project_id": project_id,
                "project": None if project_id else project,
            },
        )
        return format_listing_text(listing, heading=heading, next_call=next_call)
