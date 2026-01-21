"""Test DataviewRefreshManager with debounce and targeted refresh."""

import asyncio
import pytest
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch, call

from basic_memory.config import ProjectConfig
from basic_memory.services import EntityService
from basic_memory.sync.sync_service import SyncService
from basic_memory.sync.dataview_refresh_manager import DataviewRefreshManager
from basic_memory.models import Entity


async def create_test_file(path: Path, content: str) -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_debounce_multiple_rapid_changes():
    """
    Test that multiple rapid file changes trigger only one refresh after debounce period.
    
    Scenario: Debounce multiple rapid changes
      Given a DataviewRefreshManager with 0.1s debounce
      When 5 files are modified within 0.05s
      Then only 1 refresh should be triggered after 0.1s
    """
    # Create mock sync service
    sync_service = MagicMock()
    sync_service._refresh_entity_dataview_relations = AsyncMock()
    
    # Create manager with short debounce for testing
    manager = DataviewRefreshManager(sync_service, debounce_seconds=0.1)
    
    # Track refresh calls
    refresh_calls = []
    
    async def mock_refresh(entity_ids):
        refresh_calls.append(entity_ids)
    
    manager._refresh_entities = mock_refresh
    manager._find_impacted_entities = AsyncMock(return_value={1, 2, 3})
    
    # Trigger 5 rapid changes
    for i in range(5):
        await manager.on_file_changed(f"file{i}.md", entity_type="note", folder="notes")
        await asyncio.sleep(0.02)  # 20ms between changes
    
    # Wait for debounce to complete
    await asyncio.sleep(0.15)
    
    # Verify only 1 refresh was triggered
    assert len(refresh_calls) == 1, "Should trigger only 1 refresh after debounce"
    assert refresh_calls[0] == {1, 2, 3}, "Should refresh impacted entities"


@pytest.mark.asyncio
async def test_debounce_resets_on_new_change():
    """
    Test that debounce timer resets when new changes arrive.
    
    Scenario: Debounce timer resets
      Given a DataviewRefreshManager with 0.1s debounce
      When a file is modified
      And another file is modified 0.08s later (before debounce expires)
      Then refresh should trigger 0.1s after the LAST change
    """
    sync_service = MagicMock()
    manager = DataviewRefreshManager(sync_service, debounce_seconds=0.1)
    
    refresh_calls = []
    
    async def mock_refresh(entity_ids):
        refresh_calls.append((asyncio.get_event_loop().time(), entity_ids))
    
    manager._refresh_entities = mock_refresh
    manager._find_impacted_entities = AsyncMock(return_value={1})
    
    start_time = asyncio.get_event_loop().time()
    
    # First change at t=0
    await manager.on_file_changed("file1.md")
    
    # Second change at t=0.08s (before first debounce expires)
    await asyncio.sleep(0.08)
    await manager.on_file_changed("file2.md")
    
    # Wait for debounce to complete
    await asyncio.sleep(0.12)
    
    # Verify only 1 refresh was triggered
    assert len(refresh_calls) == 1, "Should trigger only 1 refresh"
    
    # Verify refresh happened ~0.1s after LAST change (t=0.08 + 0.1 = 0.18)
    refresh_time = refresh_calls[0][0] - start_time
    assert 0.17 < refresh_time < 0.22, f"Refresh should happen ~0.18s after start, got {refresh_time:.3f}s"


@pytest.mark.asyncio
async def test_find_impacted_entities_by_folder():
    """
    Test that entities with Dataview queries matching changed folder are found.
    
    Scenario: Find entities by folder
      Given a milestone with query "FROM 'product-memories'"
      When a file in "product-memories/" is modified
      Then the milestone should be identified as impacted
    """
    sync_service = MagicMock()
    manager = DataviewRefreshManager(sync_service)
    
    # Mock the repository to return entities with queries
    sync_service.entity_repository.find_all = AsyncMock(return_value=[
        MagicMock(
            id=1,
            file_path="milestone.md",
            content='```dataview\nFROM "product-memories"\n```'
        )
    ])
    
    # Test finding impacted entities with new signature
    changes = {
        "product-memories/us-001.md": {
            "type": "user-story",
            "folder": "product-memories",
            "metadata": {}
        }
    }
    impacted = await manager._find_impacted_entities(changes)
    
    # Should find the milestone
    assert 1 in impacted, "Milestone with matching FROM clause should be impacted"


@pytest.mark.asyncio
async def test_find_impacted_entities_by_type():
    """
    Test that entities with Dataview queries matching changed entity type are found.
    
    Scenario: Find entities by type
      Given a milestone with query "WHERE type = 'user-story'"
      When a user-story is modified
      Then the milestone should be identified as impacted
    """
    sync_service = MagicMock()
    manager = DataviewRefreshManager(sync_service)
    
    # Mock the repository to return entities with queries (no FROM clause = queries everything)
    sync_service.entity_repository.find_all = AsyncMock(return_value=[
        MagicMock(
            id=1,
            file_path="milestone.md",
            content='```dataview\nTABLE status\nWHERE type = "user-story"\n```'
        )
    ])
    
    # Test finding impacted entities with new signature
    changes = {
        "product-memories/us-001.md": {
            "type": "user-story",
            "folder": "product-memories",
            "metadata": {}
        }
    }
    impacted = await manager._find_impacted_entities(changes)
    
    # Should find the milestone (no FROM clause = always impacted)
    assert 1 in impacted, "Milestone with no FROM clause should be impacted"


@pytest.mark.asyncio
async def test_no_refresh_when_no_impacted_entities():
    """
    Test that no refresh is triggered when no entities are impacted.
    
    Scenario: No impacted entities
      Given no entities with Dataview queries
      When a file is modified
      Then no refresh should be triggered
    """
    sync_service = MagicMock()
    manager = DataviewRefreshManager(sync_service, debounce_seconds=0.05)
    
    refresh_calls = []
    
    async def mock_refresh(entity_ids):
        refresh_calls.append(entity_ids)
    
    manager._refresh_entities = mock_refresh
    manager._find_impacted_entities = AsyncMock(return_value=set())  # No impacted entities
    
    # Trigger change
    await manager.on_file_changed("file.md")
    
    # Wait for debounce
    await asyncio.sleep(0.1)
    
    # Verify no refresh was triggered
    assert len(refresh_calls) == 0, "Should not trigger refresh when no entities impacted"


@pytest.mark.asyncio
async def test_refresh_only_impacted_entities():
    """
    Test that only impacted entities are refreshed, not all entities.
    
    Scenario: Refresh only impacted entities
      Given 3 entities with Dataview queries
      When a file change impacts only 2 of them
      Then only those 2 entities should be refreshed
    """
    sync_service = MagicMock()
    sync_service._refresh_entity_dataview_relations = AsyncMock()
    
    # Mock entity repository
    entity1 = MagicMock(id=1, file_path="entity1.md", permalink="entity1")
    entity2 = MagicMock(id=2, file_path="entity2.md", permalink="entity2")
    
    async def mock_find_by_id(entity_id):
        if entity_id == 1:
            return entity1
        elif entity_id == 2:
            return entity2
        return None
    
    sync_service.entity_repository.find_by_id = mock_find_by_id
    
    # Mock file service
    sync_service.file_service.read_file_content = AsyncMock(return_value="# Test content")
    
    manager = DataviewRefreshManager(sync_service, debounce_seconds=0.05)
    manager._find_impacted_entities = AsyncMock(return_value={1, 2})  # Only entities 1 and 2 impacted
    
    # Trigger change
    await manager.on_file_changed("file.md")
    
    # Wait for debounce
    await asyncio.sleep(0.1)
    
    # Verify only impacted entities were refreshed
    assert sync_service._refresh_entity_dataview_relations.call_count == 2
    calls = sync_service._refresh_entity_dataview_relations.call_args_list
    refreshed_entities = {call[0][0].id for call in calls}
    assert refreshed_entities == {1, 2}, "Should refresh only impacted entities"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_integration_debounce_with_real_sync(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """
    Integration test: Verify debounce works with real sync service.
    
    Scenario: Integration with real sync
      Given a milestone with a Dataview query
      And a DataviewRefreshManager attached to sync_service
      When multiple user stories are created rapidly
      Then only 1 refresh should be triggered after debounce
    """
    project_dir = project_config.home
    
    # Create milestone with Dataview query
    milestone_content = dedent("""
        ---
        title: Milestone Integration
        type: milestone
        ---
        # Milestone Integration
        
        ```dataview
        LIST
        FROM "product-memories"
        WHERE type = "user-story"
        ```
    """)
    await create_test_file(project_dir / "milestone-integration.md", milestone_content)
    
    # Initial sync
    await sync_service.sync(project_config.home)
    
    # Get milestone entity
    milestone = await entity_service.get_by_permalink("milestone-integration")
    assert milestone is not None
    
    # Create manager and attach to sync service
    manager = DataviewRefreshManager(sync_service, debounce_seconds=0.1)
    
    # Mock _find_impacted_entities to return the milestone
    async def mock_find_impacted(changes):
        return {milestone.id}
    
    manager._find_impacted_entities = mock_find_impacted
    
    # Track refresh calls
    refresh_calls = []
    original_refresh = sync_service._refresh_entity_dataview_relations
    
    async def tracked_refresh(entity: Entity, file_content: str):
        refresh_calls.append(entity.id)
        return await original_refresh(entity, file_content)
    
    with patch.object(sync_service, '_refresh_entity_dataview_relations', side_effect=tracked_refresh):
        # Create 3 user stories rapidly
        for i in range(1, 4):
            us_content = dedent(f"""
                ---
                title: US-{i:03d} Story {i}
                type: user-story
                ---
                # US-{i:03d} Story {i}
            """)
            await create_test_file(
                project_dir / "product-memories" / f"us-{i:03d}.md", us_content
            )
            # Notify manager
            await manager.on_file_changed(
                f"product-memories/us-{i:03d}.md",
                entity_type="user-story",
                folder="product-memories"
            )
            await asyncio.sleep(0.02)  # 20ms between changes
        
        # Wait for debounce
        await asyncio.sleep(0.15)
        
        # Verify only 1 refresh was triggered for the milestone
        milestone_refresh_calls = [call for call in refresh_calls if call == milestone.id]
        
        assert len(milestone_refresh_calls) == 1, (
            f"Should trigger only 1 refresh for milestone, got {len(milestone_refresh_calls)}"
        )


@pytest.mark.asyncio
async def test_concurrent_debounce_tasks():
    """
    Test that concurrent debounce tasks are handled correctly.
    
    Scenario: Concurrent debounce tasks
      Given a DataviewRefreshManager
      When multiple changes arrive while a debounce is in progress
      Then the previous debounce task should be cancelled
      And only the latest debounce task should complete
    """
    sync_service = MagicMock()
    manager = DataviewRefreshManager(sync_service, debounce_seconds=0.1)
    
    refresh_calls = []
    
    async def mock_refresh(entity_ids):
        refresh_calls.append(entity_ids)
    
    manager._refresh_entities = mock_refresh
    manager._find_impacted_entities = AsyncMock(return_value={1})
    
    # Trigger first change
    await manager.on_file_changed("file1.md")
    first_task = manager._debounce_task
    
    # Wait a bit
    await asyncio.sleep(0.05)
    
    # Trigger second change (should cancel first task)
    await manager.on_file_changed("file2.md")
    second_task = manager._debounce_task
    
    # Wait for second debounce to complete
    await asyncio.sleep(0.15)
    
    # Verify first task was cancelled or completed
    assert first_task is not None and (first_task.cancelled() or first_task.done()), "First debounce task should be cancelled or done"
    assert second_task is not None and second_task.done(), "Second debounce task should be done"
    
    # Verify only 1 refresh was triggered
    assert len(refresh_calls) == 1, "Should trigger only 1 refresh"


@pytest.mark.asyncio
async def test_empty_pending_changes():
    """
    Test that debounce handles empty pending changes gracefully.
    
    Scenario: Empty pending changes
      Given a DataviewRefreshManager
      When debounce completes but pending_changes is empty
      Then no refresh should be triggered
    """
    sync_service = MagicMock()
    manager = DataviewRefreshManager(sync_service, debounce_seconds=0.05)
    
    refresh_calls = []
    
    async def mock_refresh(entity_ids):
        refresh_calls.append(entity_ids)
    
    manager._refresh_entities = mock_refresh
    
    # Manually trigger debounce with empty pending changes
    manager._pending_changes = {}
    await manager._debounced_refresh()
    
    # Verify no refresh was triggered
    assert len(refresh_calls) == 0, "Should not trigger refresh with empty pending changes"
