"""Test that relation resolution happens in the background."""

import pytest

from basic_memory.api.routers.knowledge_router import resolve_relations_background


@pytest.mark.asyncio
async def test_resolve_relations_background_success():
    """Test that background relation resolution calls sync service correctly."""
    class StubSyncService:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def resolve_relations(self, *, entity_id: int) -> None:
            self.calls.append(entity_id)

    sync_service = StubSyncService()

    entity_id = 123
    entity_permalink = "test/entity"

    # Call the background function
    await resolve_relations_background(sync_service, entity_id, entity_permalink)

    # Verify sync service was called with the entity_id
    assert sync_service.calls == [entity_id]


@pytest.mark.asyncio
async def test_resolve_relations_background_handles_errors():
    """Test that background relation resolution handles errors gracefully."""
    class StubSyncService:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def resolve_relations(self, *, entity_id: int) -> None:
            self.calls.append(entity_id)
            raise Exception("Test error")

    sync_service = StubSyncService()

    entity_id = 123
    entity_permalink = "test/entity"

    # Call should not raise - errors are logged
    await resolve_relations_background(sync_service, entity_id, entity_permalink)

    # Verify sync service was called
    assert sync_service.calls == [entity_id]
