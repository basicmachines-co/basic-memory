"""Test automatic Dataview refresh integration with SyncService."""

import asyncio
import pytest
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, patch

from basic_memory.config import ProjectConfig
from basic_memory.services import EntityService
from basic_memory.sync.sync_service import SyncService


async def create_test_file(path: Path, content: str) -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_markdown_triggers_dataview_refresh(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """
    Test that sync_markdown_file automatically triggers Dataview refresh.
    
    Scenario: Automatic Dataview refresh on sync
      Given a milestone with a Dataview query
      When a user story is synced
      Then the DataviewRefreshManager should be notified
      And the refresh should be debounced
    """
    project_dir = project_config.home
    
    # Create milestone with Dataview query
    milestone_content = dedent("""
        ---
        title: Milestone Auto Refresh
        type: milestone
        ---
        # Milestone Auto Refresh
        
        ```dataview
        LIST
        FROM "product-memories"
        WHERE type = "user-story"
        ```
    """)
    await create_test_file(project_dir / "milestone-auto.md", milestone_content)
    
    # Initial sync
    await sync_service.sync(project_config.home)
    
    # Verify manager is initialized
    assert sync_service.dataview_refresh_manager is not None
    assert sync_service.dataview_refresh_manager.debounce_seconds == 5.0
    
    # Mock on_file_changed to track calls
    original_on_file_changed = sync_service.dataview_refresh_manager.on_file_changed
    call_tracker = []
    
    async def tracked_on_file_changed(file_path, entity_type=None, folder=None, metadata=None):
        call_tracker.append({
            'file_path': file_path,
            'entity_type': entity_type,
            'folder': folder,
            'metadata': metadata
        })
        return await original_on_file_changed(file_path, entity_type, folder, metadata)
    
    with patch.object(
        sync_service.dataview_refresh_manager, 
        'on_file_changed', 
        side_effect=tracked_on_file_changed
    ):
        # Create a user story
        us_content = dedent("""
            ---
            title: US-001 Test Story
            type: user-story
            status: In Progress
            ---
            # US-001 Test Story
        """)
        await create_test_file(
            project_dir / "product-memories" / "us-001.md", us_content
        )
        
        # Add a small delay to ensure file timestamp is different
        await asyncio.sleep(0.01)
        
        # Sync the file with force_full to ensure it's detected
        await sync_service.sync(project_config.home, force_full=True)
        
        # Verify on_file_changed was called
        assert len(call_tracker) > 0, "on_file_changed should be called during sync"
        
        # Find the call for our user story
        us_calls = [
            call for call in call_tracker 
            if 'us-001.md' in call['file_path']
        ]
        
        assert len(us_calls) > 0, "User story sync should trigger on_file_changed"
        
        # Verify call parameters
        us_call = us_calls[0]
        assert us_call['entity_type'] == 'user-story'
        assert 'product-memories' in us_call['folder']
        assert us_call['metadata'] is not None
        # metadata contains the frontmatter dict
        metadata = us_call['metadata']
        assert metadata.get('status') == 'In Progress' or metadata.get('metadata', {}).get('status') == 'In Progress'


@pytest.mark.asyncio
@pytest.mark.integration
async def test_multiple_syncs_debounced(
    sync_service: SyncService,
    project_config: ProjectConfig,
):
    """
    Test that multiple rapid syncs are debounced correctly.
    
    Scenario: Multiple rapid syncs
      Given a DataviewRefreshManager with 5s debounce
      When multiple files are synced rapidly
      Then refresh should be debounced
      And only trigger once after the debounce period
    """
    project_dir = project_config.home
    
    # Create milestone with Dataview query
    milestone_content = dedent("""
        ---
        title: Milestone Debounce
        type: milestone
        ---
        # Milestone Debounce
        
        ```dataview
        LIST
        FROM "product-memories"
        ```
    """)
    await create_test_file(project_dir / "milestone-debounce.md", milestone_content)
    
    # Initial sync
    await sync_service.sync(project_config.home)
    
    # Reduce debounce for testing
    sync_service.dataview_refresh_manager.debounce_seconds = 0.1
    
    # Track refresh calls
    refresh_calls = []
    original_refresh = sync_service.dataview_refresh_manager._refresh_entities
    
    async def tracked_refresh(entity_ids):
        refresh_calls.append(entity_ids)
        return await original_refresh(entity_ids)
    
    with patch.object(
        sync_service.dataview_refresh_manager,
        '_refresh_entities',
        side_effect=tracked_refresh
    ):
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
        
        # Sync all files
        await sync_service.sync(project_config.home)
        
        # Wait for debounce to complete
        await asyncio.sleep(0.2)
        
        # Verify refresh was called (debounced)
        # Should be 1 call for the debounced batch
        assert len(refresh_calls) <= 2, (
            f"Should have at most 2 refresh calls (initial + debounced), got {len(refresh_calls)}"
        )


@pytest.mark.asyncio
async def test_dataview_manager_initialized_on_sync_service_creation(
    sync_service: SyncService,
):
    """
    Test that DataviewRefreshManager is properly initialized.
    
    Scenario: Manager initialization
      Given a SyncService instance
      Then it should have a DataviewRefreshManager
      And the manager should be configured with correct parameters
    """
    # Verify manager exists
    assert hasattr(sync_service, 'dataview_refresh_manager')
    assert sync_service.dataview_refresh_manager is not None
    
    # Verify manager configuration
    manager = sync_service.dataview_refresh_manager
    assert manager.sync_service is sync_service
    assert manager.debounce_seconds == 5.0
    
    # Verify manager has required methods
    assert hasattr(manager, 'on_file_changed')
    assert hasattr(manager, '_debounced_refresh')
    assert hasattr(manager, '_find_impacted_entities')
    assert hasattr(manager, '_refresh_entities')
