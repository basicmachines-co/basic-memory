"""Test that relation resolution happens in the background."""

import pytest
from unittest.mock import AsyncMock

from basic_memory.api.routers.knowledge_router import resolve_relations_background


@pytest.mark.asyncio
async def test_resolve_relations_background_success():
    """Test that background relation resolution calls sync service correctly."""
    # Create mocks
    sync_service = AsyncMock()
    sync_service.resolve_relations = AsyncMock(return_value=None)

    entity_id = 123
    entity_permalink = "test/entity"

    # Call the background function
    await resolve_relations_background(sync_service, entity_id, entity_permalink)

    # Verify sync service was called with the entity_id
    sync_service.resolve_relations.assert_called_once_with(entity_id=entity_id)


@pytest.mark.asyncio
async def test_resolve_relations_background_handles_errors():
    """Test that background relation resolution handles errors gracefully."""
    # Create mock that raises an exception
    sync_service = AsyncMock()
    sync_service.resolve_relations = AsyncMock(side_effect=Exception("Test error"))

    entity_id = 123
    entity_permalink = "test/entity"

    # Call should not raise - errors are logged
    await resolve_relations_background(sync_service, entity_id, entity_permalink)

    # Verify sync service was called
    sync_service.resolve_relations.assert_called_once_with(entity_id=entity_id)
