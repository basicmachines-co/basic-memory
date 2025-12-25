"""Canvas creation tool for Basic Memory MCP server.

This tool creates Obsidian canvas files (.canvas) using the JSON Canvas 1.0 spec.
"""

import json
from typing import Dict, List, Any, Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.project_context import get_active_project
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_put, call_post, resolve_entity_id
from basic_memory.telemetry import track_mcp_tool


@mcp.tool(
    description="Create an Obsidian canvas file to visualize concepts and connections.",
)
async def canvas(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    title: str,
    folder: str,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Create an Obsidian canvas file with the provided nodes and edges.

    This tool creates a .canvas file compatible with Obsidian's Canvas feature,
    allowing visualization of relationships between concepts or documents.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode → project parameter → default project.
    If project unknown, use list_memory_projects() or recent_activity() first.

    For the full JSON Canvas 1.0 specification, see the 'spec://canvas' resource.

    Args:
        project: Project name to create canvas in. Optional - server will resolve using hierarchy.
                If unknown, use list_memory_projects() to discover available projects.
        nodes: List of node objects following JSON Canvas 1.0 spec
        edges: List of edge objects following JSON Canvas 1.0 spec
        title: The title of the canvas (will be saved as title.canvas)
        folder: Folder path relative to project root where the canvas should be saved.
                Use forward slashes (/) as separators. Examples: "diagrams", "projects/2025", "visual/maps"
        context: Optional FastMCP context for performance caching.

    Returns:
        A summary of the created canvas file

    Important Notes:
    - When referencing files, use the exact file path as shown in Obsidian
      Example: "folder/Document Name.md" (not permalink format)
    - For file nodes, the "file" attribute must reference an existing file
    - Nodes require id, type, x, y, width, height properties
    - Edges require id, fromNode, toNode properties
    - Position nodes in a logical layout (x,y coordinates in pixels)
    - Use color attributes ("1"-"6" or hex) for visual organization

    Basic Structure:
    ```json
    {
      "nodes": [
        {
          "id": "node1",
          "type": "file",  // Options: "file", "text", "link", "group"
          "file": "folder/Document.md",
          "x": 0,
          "y": 0,
          "width": 400,
          "height": 300
        }
      ],
      "edges": [
        {
          "id": "edge1",
          "fromNode": "node1",
          "toNode": "node2",
          "label": "connects to"
        }
      ]
    }
    ```

    Examples:
        # Create canvas in project
        canvas("my-project", nodes=[...], edges=[...], title="My Canvas", folder="diagrams")

        # Create canvas in work project
        canvas("work-project", nodes=[...], edges=[...], title="Process Flow", folder="visual/maps")

    Raises:
        ToolError: If project doesn't exist or folder path is invalid
    """
    track_mcp_tool("canvas")
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)

        # Ensure path has .canvas extension
        file_title = title if title.endswith(".canvas") else f"{title}.canvas"
        file_path = f"{folder}/{file_title}"

        # Create canvas data structure
        canvas_data = {"nodes": nodes, "edges": edges}

        # Convert to JSON
        canvas_json = json.dumps(canvas_data, indent=2)

        # Try to create the canvas file first (optimistic create)
        logger.info(f"Creating canvas file: {file_path} in project {project}")
        try:
            response = await call_post(
                client,
                f"/v2/projects/{active_project.id}/resource",
                json={"file_path": file_path, "content": canvas_json},
            )
            action = "Created"
        except Exception as e:
            # If creation failed due to conflict (already exists), try to update
            if (
                "409" in str(e)
                or "conflict" in str(e).lower()
                or "already exists" in str(e).lower()
            ):
                logger.info(f"Canvas file exists, updating instead: {file_path}")
                try:
                    entity_id = await resolve_entity_id(client, active_project.id, file_path)
                    # For update, send content in JSON body
                    response = await call_put(
                        client,
                        f"/v2/projects/{active_project.id}/resource/{entity_id}",
                        json={"content": canvas_json},
                    )
                    action = "Updated"
                except Exception as update_error:
                    # Re-raise the original error if update also fails
                    raise e from update_error
            else:
                # Re-raise if it's not a conflict error
                raise

        # Parse response
        result = response.json()
        logger.debug(result)

        # Build summary
        summary = [f"# {action}: {file_path}", "\nThe canvas is ready to open in Obsidian."]

        return "\n".join(summary)
