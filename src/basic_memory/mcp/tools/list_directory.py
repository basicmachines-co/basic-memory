"""List directory tool for Basic Memory MCP server."""

from typing import Annotated, Any, Literal, Optional

from loguru import logger
from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.server import mcp
from basic_memory.schemas.directory import (
    DEFAULT_DIRECTORY_PAGE_SIZE,
    MAX_DIRECTORY_PAGE_SIZE,
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
            f"Listing directory '{dir_name}' in project {project} with depth={depth}, glob='{file_name_glob}'"
        )

        # Import here to avoid circular import
        from basic_memory.mcp.clients import DirectoryClient

        # Use typed DirectoryClient for API calls
        directory_client = DirectoryClient(client, active_project.external_id)
        listing = await directory_client.list(
            dir_name,
            depth=depth,
            file_name_glob=file_name_glob,
            page=page,
            page_size=page_size,
        )

        if output_format == "json":
            return listing.model_dump(mode="json")

        nodes = [node.model_dump(mode="json", exclude_none=True) for node in listing.nodes]

        if not nodes:
            filter_desc = ""
            if file_name_glob:
                filter_desc = f" matching '{file_name_glob}'"
            return f"No files found in directory '{dir_name}'{filter_desc}"

        # Format the results
        output_lines = []
        if file_name_glob:
            output_lines.append(
                f"Files in '{dir_name}' matching '{file_name_glob}' (depth {depth}):"
            )
        else:
            output_lines.append(f"Contents of '{dir_name}' (depth {depth}):")
        output_lines.append(
            f"Page {listing.page} (page size {listing.page_size}, {listing.total} total items)"
        )
        output_lines.append("")

        # Group by type and sort
        directories = [n for n in nodes if n["type"] == "directory"]
        files = [n for n in nodes if n["type"] == "file"]

        # Sort by name
        directories.sort(key=lambda x: x["name"])
        files.sort(key=lambda x: x["name"])

        # Display directories first
        for node in directories:
            path_display = node["directory_path"]
            output_lines.append(f"📁 {node['name']:<30} {path_display}")

        # Add separator if we have both directories and files
        if directories and files:
            output_lines.append("")

        # Display files with metadata
        for node in files:
            path_display = node["directory_path"]
            title = node.get("title", "")
            updated = node.get("updated_at", "")

            # Remove leading slash if present, requesting the file via read_note does not use the beginning slash'
            if path_display.startswith("/"):
                path_display = path_display[1:]

            # Format date if available
            date_str = ""
            if updated:
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:  # pragma: no cover
                    date_str = updated[:10] if len(updated) >= 10 else ""

            # Create formatted line
            file_line = f"📄 {node['name']:<30} {path_display}"
            if title and title != node["name"]:
                file_line += f" | {title}"
            if date_str:
                file_line += f" | {date_str}"
            # Web-app deep links are built from the note's external_id; hosted
            # MCP appends a link template that references it, so the id must be
            # visible in the listing the agent actually reads.
            external_id = node.get("external_id")
            if external_id:
                file_line += f" | id: {external_id}"

            output_lines.append(file_line)

        # Add summary
        output_lines.append("")
        total_count = len(directories) + len(files)
        summary_parts = []
        if directories:
            summary_parts.append(
                f"{len(directories)} director{'y' if len(directories) == 1 else 'ies'}"
            )
        if files:
            summary_parts.append(f"{len(files)} file{'s' if len(files) != 1 else ''}")

        output_lines.append(f"Total: {total_count} items ({', '.join(summary_parts)})")

        if listing.has_more:
            next_page = listing.page + 1
            continuation_args = [
                f"dir_name={dir_name!r}",
                f"depth={depth}",
                f"page={next_page}",
                f"page_size={listing.page_size}",
            ]
            if file_name_glob:
                continuation_args.append(f"file_name_glob={file_name_glob!r}")
            if project:
                continuation_args.append(f"project={project!r}")
            output_lines.extend(
                [
                    "",
                    "More results available. Call "
                    f"list_directory({', '.join(continuation_args)}) to continue.",
                ]
            )

        return "\n".join(output_lines)
