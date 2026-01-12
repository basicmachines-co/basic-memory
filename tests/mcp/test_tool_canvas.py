"""Tests for canvas tool that exercise the full stack with SQLite."""

import json
from pathlib import Path

import pytest

from basic_memory.mcp.tools import canvas


@pytest.mark.asyncio
async def test_create_canvas(app, project_config, test_project):
    """Test creating a new canvas file.

    Should:
    - Create canvas file with correct content
    - Create entity in database
    - Return successful status
    """
    # Test data
    nodes = [
        {
            "id": "node1",
            "type": "text",
            "text": "Test Node",
            "x": 100,
            "y": 200,
            "width": 400,
            "height": 300,
        }
    ]
    edges = [{"id": "edge1", "fromNode": "node1", "toNode": "node2", "label": "connects to"}]
    title = "test-canvas"
    folder = "visualizations"

    # Execute
    result = await canvas.fn(
        project=test_project.name, nodes=nodes, edges=edges, title=title, folder=folder
    )

    # Verify result message
    assert result
    assert "Created: visualizations/test-canvas" in result
    assert "The canvas is ready to open in Obsidian" in result

    # Verify file was created
    file_path = Path(project_config.home) / folder / f"{title}.canvas"
    assert file_path.exists()

    # Verify content is correct
    content = json.loads(file_path.read_text(encoding="utf-8"))
    assert content["nodes"] == nodes
    assert content["edges"] == edges


@pytest.mark.asyncio
async def test_create_canvas_with_extension(app, project_config, test_project):
    """Test creating a canvas file with .canvas extension already in the title."""
    # Test data
    nodes = [
        {
            "id": "node1",
            "type": "text",
            "text": "Extension Test",
            "x": 100,
            "y": 200,
            "width": 400,
            "height": 300,
        }
    ]
    edges = []
    title = "extension-test.canvas"  # Already has extension
    folder = "visualizations"

    # Execute
    result = await canvas.fn(
        project=test_project.name, nodes=nodes, edges=edges, title=title, folder=folder
    )

    # Verify
    assert "Created: visualizations/extension-test.canvas" in result

    # Verify file exists with correct name (shouldn't have double extension)
    file_path = Path(project_config.home) / folder / title
    assert file_path.exists()

    # Verify content
    content = json.loads(file_path.read_text(encoding="utf-8"))
    assert content["nodes"] == nodes


@pytest.mark.asyncio
async def test_update_existing_canvas(app, project_config, test_project):
    """Test updating an existing canvas file."""
    # First create a canvas
    nodes = [
        {
            "id": "initial",
            "type": "text",
            "text": "Initial content",
            "x": 0,
            "y": 0,
            "width": 200,
            "height": 100,
        }
    ]
    edges = []
    title = "update-test"
    folder = "visualizations"

    # Create initial canvas
    await canvas.fn(project=test_project.name, nodes=nodes, edges=edges, title=title, folder=folder)

    # Verify file exists
    file_path = Path(project_config.home) / folder / f"{title}.canvas"
    assert file_path.exists()

    # Now update with new content
    updated_nodes = [
        {
            "id": "updated",
            "type": "text",
            "text": "Updated content",
            "x": 100,
            "y": 100,
            "width": 300,
            "height": 200,
        }
    ]
    updated_edges = [
        {"id": "new-edge", "fromNode": "updated", "toNode": "other", "label": "new connection"}
    ]

    # Execute update
    result = await canvas.fn(
        project=test_project.name,
        nodes=updated_nodes,
        edges=updated_edges,
        title=title,
        folder=folder,
    )

    # Verify result indicates update
    assert "Updated: visualizations/update-test.canvas" in result

    # Verify content was updated
    content = json.loads(file_path.read_text(encoding="utf-8"))
    assert content["nodes"] == updated_nodes
    assert content["edges"] == updated_edges


@pytest.mark.asyncio
async def test_create_canvas_with_nested_folders(app, project_config, test_project):
    """Test creating a canvas in nested folders that don't exist yet."""
    # Test data
    nodes = [
        {
            "id": "test",
            "type": "text",
            "text": "Nested folder test",
            "x": 0,
            "y": 0,
            "width": 200,
            "height": 100,
        }
    ]
    edges = []
    title = "nested-test"
    folder = "visualizations/nested/folders"  # Deep path

    # Execute
    result = await canvas.fn(
        project=test_project.name, nodes=nodes, edges=edges, title=title, folder=folder
    )

    # Verify
    assert "Created: visualizations/nested/folders/nested-test.canvas" in result

    # Verify folders and file were created
    file_path = Path(project_config.home) / folder / f"{title}.canvas"
    assert file_path.exists()
    assert file_path.parent.exists()


@pytest.mark.asyncio
async def test_create_canvas_complex_content(app, project_config, test_project):
    """Test creating a canvas with complex content structures."""
    # Test data - more complex structure with all node types
    nodes = [
        {
            "id": "text-node",
            "type": "text",
            "text": "# Heading\n\nThis is a test with *markdown* formatting",
            "x": 100,
            "y": 100,
            "width": 400,
            "height": 300,
            "color": "4",  # Using a preset color
        },
        {
            "id": "file-node",
            "type": "file",
            "file": "test/test-file.md",  # Reference a file
            "x": 600,
            "y": 100,
            "width": 400,
            "height": 300,
            "color": "#FF5500",  # Using hex color
        },
        {
            "id": "link-node",
            "type": "link",
            "url": "https://example.com",
            "x": 100,
            "y": 500,
            "width": 400,
            "height": 200,
        },
        {
            "id": "group-node",
            "type": "group",
            "label": "Group Label",
            "x": 600,
            "y": 500,
            "width": 600,
            "height": 400,
        },
    ]

    edges = [
        {
            "id": "edge1",
            "fromNode": "text-node",
            "toNode": "file-node",
            "label": "references",
            "fromSide": "right",
            "toSide": "left",
        },
        {
            "id": "edge2",
            "fromNode": "link-node",
            "toNode": "group-node",
            "label": "belongs to",
            "color": "6",
        },
    ]

    title = "complex-test"
    folder = "visualizations"

    # Create a test file that we're referencing
    test_file_path = Path(project_config.home) / "test/test-file.md"
    test_file_path.parent.mkdir(parents=True, exist_ok=True)
    test_file_path.write_text("# Test File\nThis is referenced by the canvas")

    # Execute
    result = await canvas.fn(
        project=test_project.name, nodes=nodes, edges=edges, title=title, folder=folder
    )

    # Verify
    assert "Created: visualizations/complex-test.canvas" in result

    # Verify file was created
    file_path = Path(project_config.home) / folder / f"{title}.canvas"
    assert file_path.exists()

    # Verify content is correct with all complex structures
    content = json.loads(file_path.read_text(encoding="utf-8"))
    assert len(content["nodes"]) == 4
    assert len(content["edges"]) == 2

    # Verify specific content elements are preserved
    assert any(node["type"] == "text" and "#" in node["text"] for node in content["nodes"])
    assert any(
        node["type"] == "file" and "test-file.md" in node["file"] for node in content["nodes"]
    )
    assert any(node["type"] == "link" and "example.com" in node["url"] for node in content["nodes"])
    assert any(
        node["type"] == "group" and "Group Label" == node["label"] for node in content["nodes"]
    )

    # Verify edge properties
    assert any(
        edge["fromSide"] == "right" and edge["toSide"] == "left" for edge in content["edges"]
    )
    assert any(edge["label"] == "belongs to" and edge["color"] == "6" for edge in content["edges"])
