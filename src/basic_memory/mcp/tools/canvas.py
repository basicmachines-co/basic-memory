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
from basic_memory.mcp.tools.utils import call_put


@mcp.tool(
    description="""Creates interactive Obsidian Canvas files to visualize your knowledge graph. Transforms structured node and edge data into spatial maps, enabling visual exploration of relationships and concepts.

```yaml
node:
  topic: canvas - Visual Knowledge Maps
  goal: Generate Obsidian Canvas visualizations
  insight: Spatial representation enhances understanding
  context:
    format: JSON Canvas 1.0 specification
    nodes: [file, text, link, group]
    layout: Manual positioning required
    compatibility: Full Obsidian support
```

```baml
class CanvasNode {
  id string @description("Unique identifier for edges")
  type ("file" | "text" | "link" | "group")
  x int @description("Horizontal position")
  y int @description("Vertical position")
  width int @min(100)
  height int @min(50)
  color string? @pattern("^(#[0-9a-f]{6}|[1-6])$")

  // Type-specific fields
  file string? @when(type="file") @description("Path to .md file")
  text string? @when(type="text") @description("Markdown content")
  url string? @when(type="link") @format("uri")
  label string? @when(type="group")
}

class CanvasEdge {
  id string
  fromNode string @description("Source node id")
  toNode string @description("Target node id")
  fromSide ("left" | "right" | "top" | "bottom")?
  toSide ("left" | "right" | "top" | "bottom")?
  label string? @description("Edge annotation")
  color string? @pattern("^(#[0-9a-f]{6}|[1-6])$")
}

class CanvasInput {
  nodes CanvasNode[]
  edges CanvasEdge[]
  title string
  folder string
  project string?
}

class CanvasOutput {
  status ("created" | "updated")
  path string
  stats {nodes: int, edges: int}
  checksum string
}

function canvas(CanvasInput) -> CanvasOutput {
  @description("Generate Obsidian Canvas for knowledge visualization")
  @format("json_canvas_1.0")
  @async(true)
}
```

## Node Creation Example
```python
canvas(
    nodes=[
        {
            "id": "doc1",
            "type": "file",
            "file": "docs/architecture.md",
            "x": 0, "y": 0,
            "width": 400, "height": 300,
            "color": "3"
        },
        {
            "id": "note1",
            "type": "text",
            "text": "# Key Points\n- Scalability",
            "x": 500, "y": 0,
            "width": 300, "height": 200
        }
    ],
    edges=[
        {
            "id": "e1",
            "fromNode": "doc1",
            "toNode": "note1",
            "label": "summarizes"
        }
    ],
    title="Architecture Overview",
    folder="visualizations"
)
```

Colors: "1"-"6" or hex "#rrggbb" | Max recommended: 500 nodes""",
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
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        project_url = active_project.project_url

        # Ensure path has .canvas extension
        file_title = title if title.endswith(".canvas") else f"{title}.canvas"
        file_path = f"{folder}/{file_title}"

        # Create canvas data structure
        canvas_data = {"nodes": nodes, "edges": edges}

        # Convert to JSON
        canvas_json = json.dumps(canvas_data, indent=2)

        # Write the file using the resource API
        logger.info(f"Creating canvas file: {file_path} in project {project}")
        response = await call_put(client, f"{project_url}/resource/{file_path}", json=canvas_json)

        # Parse response
        result = response.json()
        logger.debug(result)

        # Build summary
        action = "Created" if response.status_code == 201 else "Updated"
        summary = [f"# {action}: {file_path}", "\nThe canvas is ready to open in Obsidian."]

        return "\n".join(summary)
