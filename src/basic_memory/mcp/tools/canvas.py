"""Canvas creation tool for Basic Memory MCP server.

This tool creates Obsidian canvas files (.canvas) using the JSON Canvas 1.0 spec.
"""

import json
from typing import Dict, List, Any, Optional

from loguru import logger

from basic_memory.mcp.async_client import client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_put
from basic_memory.mcp.project_session import get_active_project


@mcp.tool(
    description="Create an Obsidian canvas file to visualize concepts and connections.",
)
async def canvas(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    title: str,
    folder: str,
    project: Optional[str] = None,
) -> str:
    """Create an Obsidian canvas file with the provided nodes and edges.

    This tool creates a .canvas file compatible with Obsidian's Canvas feature,
    allowing visualization of relationships between concepts or documents.

    For the full JSON Canvas 1.0 specification, see the 'spec://canvas' resource.

    Args:
        nodes: List of node objects following JSON Canvas 1.0 spec
        edges: List of edge objects following JSON Canvas 1.0 spec
        title: The title of the canvas (will be saved as title.canvas)
        folder: The folder where the file should be saved
        project: Optional project name to create canvas in. If not provided, uses current active project.

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
        # Create canvas in current project
        canvas(nodes=[...], edges=[...], title="My Canvas", folder="diagrams")

        # Create canvas in specific project
        canvas(nodes=[...], edges=[...], title="My Canvas", folder="diagrams", project="work-project")
    """
    active_project = get_active_project(project)
    project_url = active_project.project_url

    # Ensure path has .canvas extension
    file_title = title if title.endswith(".canvas") else f"{title}.canvas"
    file_path = f"{folder}/{file_title}"

    # Create canvas data structure
    canvas_data = {"nodes": nodes, "edges": edges}

    # Convert to JSON
    canvas_json = json.dumps(canvas_data, indent=2)

    # Write the file using the resource API
    logger.info(f"Creating canvas file: {file_path}")
    response = await call_put(client, f"{project_url}/resource/{file_path}", json=canvas_json)

    # Parse response
    result = response.json()
    logger.debug(result)

    # Build summary
    action = "Created" if response.status_code == 201 else "Updated"
    summary = [f"# {action}: {file_path}", "\nThe canvas is ready to open in Obsidian."]

    return "\n".join(summary)
