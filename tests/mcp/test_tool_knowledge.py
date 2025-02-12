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
    permalink = await notes.write_note(
        title="Test Note",
        folder="test",
        content="""
# Test\nThis is a test note
- [note] First observation
""",
        tags=["test", "documentation"],
    )

    # Get the entity
    entity = await get_entity(permalink)

    # Verify entity details
    assert entity.title == "Test Note"
    assert entity.permalink == "test/test-note"
    assert len(entity.observations) == 1


@pytest.mark.asyncio
async def test_get_multiple_entities(client):
    """Test retrieving multiple entities."""
    # Create two test entities
    permalink1 = await notes.write_note(
        title="Test Note 1",
        folder="test",
        content="# Test 1",
    )
    permalink2 = await notes.write_note(
        title="Test Note 2",
        folder="test",
        content="# Test 2",
    )

    # Get both entities
    request = GetEntitiesRequest(permalinks=[permalink1, permalink2])
    response = await get_entities(request)

    # Verify we got both entities
    assert len(response.entities) == 2
    permalinks = {e.permalink for e in response.entities}
    assert permalink1 in permalinks
    assert permalink2 in permalinks


@pytest.mark.asyncio
async def test_delete_entities(client):
    """Test deleting entities."""
    # Create a test entity
    permalink = await notes.write_note(
        title="Test Note",
        folder="test",
        content="# Test Note to Delete",
    )

    # Delete the entity
    request = DeleteEntitiesRequest(permalinks=[permalink])
    response = await delete_entities(request)

    # Verify deletion
    assert response.deleted is True

    # Verify entity no longer exists
    with pytest.raises(ToolError):
        await get_entity(permalink)


@pytest.mark.asyncio
async def test_get_nonexistent_entity(client):
    """Test attempting to get a non-existent entity."""
    with pytest.raises(ToolError):
        await get_entity("test/nonexistent")
