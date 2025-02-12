"""Tests for get_entity MCP tool."""

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import notes
from basic_memory.mcp.tools.knowledge import get_entity


@pytest.mark.asyncio
async def test_get_basic_entity(client):
    """Test retrieving a basic entity."""
    # First create an entity
    permalink = await notes.write_note(
        title="Test Note",
        folder="test",
        content="""
# Test\nThis is a test note
- [note] First observation
""",
        tags=["test", "documentation"],
    )

    assert permalink  # Got a valid permalink

    # Get the entity without content
    entity = await get_entity(permalink)

    # Verify entity details
    assert entity.file_path == "test/Test Note.md"
    assert entity.entity_type == "note"
    assert entity.permalink == "test/test-note"

    # Check observations
    assert len(entity.observations) == 1
    obs = entity.observations[0]
    assert obs.content == "First observation"
    assert obs.category == "note"


@pytest.mark.asyncio
async def test_get_nonexistent_entity(client):
    """Test attempting to get a non-existent entity."""
    with pytest.raises(ToolError):
        await get_entity("test/nonexistent")
