"""List directory tool for Basic Memory MCP server."""

from typing import Optional

from loguru import logger

from basic_memory.mcp.async_client import client
from basic_memory.mcp.project_session import get_active_project
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get


@mcp.tool(
    description="List directory contents with filtering and depth control.",
)
async def list_directory(
    dir_name: str = "/",
    depth: int = 1,
    file_name_glob: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
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
        project: Optional project name to delete from. If not provided, uses current active project.
    Returns:
        Formatted listing of directory contents with file metadata

    Examples:
        # List root directory contents
        list_directory()

        # List specific folder
        list_directory(dir_name="/projects")

        # Find all Python files
        list_directory(file_name_glob="*.py")

        # Deep exploration of research folder
        list_directory(dir_name="/research", depth=3)

        # Find meeting notes in projects folder
        list_directory(dir_name="/projects", file_name_glob="*meeting*")

        # Find meeting notes in a specific project
        list_directory(dir_name="/projects", file_name_glob="*meeting*", project="work-project")
    """
    active_project = get_active_project(project)
    project_url = active_project.project_url

    # Prepare query parameters
    params = {
        "dir_name": dir_name,
        "depth": str(depth),
    }
    if file_name_glob:
        params["file_name_glob"] = file_name_glob

    logger.debug(f"Listing directory '{dir_name}' with depth={depth}, glob='{file_name_glob}'")

    # Call the API endpoint
    response = await call_get(
        client,
        f"{project_url}/directory/list",
        params=params,
    )

    nodes = response.json()

    if not nodes:
        filter_desc = ""
        if file_name_glob:
            filter_desc = f" matching '{file_name_glob}'"
        return f"No files found in directory '{dir_name}'{filter_desc}"

    # Format the results
    output_lines = []
    if file_name_glob:
        output_lines.append(f"Files in '{dir_name}' matching '{file_name_glob}' (depth {depth}):")
    else:
        output_lines.append(f"Contents of '{dir_name}' (depth {depth}):")
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

    return "\n".join(output_lines)
