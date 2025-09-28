"""Test that relation resolution happens in the background."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import BackgroundTasks

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


@pytest.mark.asyncio
async def test_background_task_scheduling(test_client, test_project, monkeypatch):
    """Test that creating an entity schedules background relation resolution."""
    from basic_memory.api.routers import knowledge_router

    # Track background tasks
    background_tasks_added = []

    original_add_task = BackgroundTasks.add_task

    def mock_add_task(self, func, *args, **kwargs):
        background_tasks_added.append((func, args, kwargs))
        # Don't actually run the task in the test

    monkeypatch.setattr(BackgroundTasks, "add_task", mock_add_task)

    # Create an entity with relations
    data = {
        "title": "Test Entity with Relations",
        "folder": "test",
        "content": """# Test Entity

## Observations
- [test] This is a test observation

## Relations
- relates_to [[Other Entity]]
- depends_on [[Another Entity]]
""",
        "content_type": "text/markdown",
        "entity_type": "note",
    }

    response = test_client.put(
        f"/api/v1/projects/{test_project.permalink}/knowledge/entities/test/test-entity-with-relations",
        json=data,
    )

    assert response.status_code == 201

    # Check that a background task was scheduled
    assert len(background_tasks_added) > 0

    # Find the resolve_relations_background task
    resolve_tasks = [
        task for task in background_tasks_added
        if task[0] == resolve_relations_background
    ]

    # Should have scheduled one resolve_relations_background task
    assert len(resolve_tasks) == 1

    # Verify the task was called with correct arguments
    task_func, task_args, task_kwargs = resolve_tasks[0]
    assert task_func == resolve_relations_background
    # Should have sync_service, entity_id, and permalink as arguments
    assert len(task_args) == 3