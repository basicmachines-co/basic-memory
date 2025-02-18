"""Tests for knowledge MCP tools."""

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import notes
from basic_memory.mcp.tools.knowledge import get_entity, get_entities, delete_entities
from basic_memory.schemas.request import GetEntitiesRequest
from basic_memory.schemas.delete import DeleteEntitiesRequest


@pytest.mark.asyncio
async def test_get_single_entity(client):
    """Test retrieving a single entity."""
    # First create an entity
    result = await notes.write_note(
        title="Test Note",
        folder="test",
        content="""
# Test\nThis is a test note
- [note] First observation
""",
        tags=["test", "documentation"],
    )
    assert result

    # Get the entity
    entity = await get_entity("test/test-note")

    # Verify entity details
    assert entity.title == "Test Note"
    assert entity.permalink == "test/test-note"
    assert len(entity.observations) == 1


@pytest.mark.asyncio
async def test_get_multiple_entities(client):
    """Test retrieving multiple entities."""
    # Create two test entities
    await notes.write_note(
        title="Test Note 1",
        folder="test",
        content="# Test 1",
    )
    await notes.write_note(
        title="Test Note 2",
        folder="test",
        content="# Test 2",
    )

    # Get both entities
    request = GetEntitiesRequest(permalinks=["test/test-note-1", "test/test-note-2"])
    response = await get_entities(request)

    # Verify we got both entities
    assert len(response.entities) == 2
    permalinks = {e.permalink for e in response.entities}
    assert "test/test-note-1" in permalinks
    assert "test/test-note-2" in permalinks


@pytest.mark.asyncio
async def test_delete_entities(client):
    """Test deleting entities."""
    # Create a test entity
    await notes.write_note(
        title="Test Note",
        folder="test",
        content="# Test Note to Delete",
    )

    # Delete the entity
    request = DeleteEntitiesRequest(permalinks=["test/test-note"])
    response = await delete_entities(request)

    # Verify deletion
    assert response.deleted is True

    # Verify entity no longer exists
    with pytest.raises(ToolError):
        await get_entity("test/test-note")


@pytest.mark.asyncio
async def test_get_nonexistent_entity(client):
    """Test attempting to get a non-existent entity."""
    with pytest.raises(ToolError):
        await get_entity("test/nonexistent")
