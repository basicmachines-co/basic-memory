"""Test Dataview relations auto-update functionality (US-002)."""

import pytest
from pathlib import Path
from textwrap import dedent

from basic_memory.config import ProjectConfig
from basic_memory.services import EntityService
from basic_memory.sync.sync_service import SyncService


async def create_test_file(path: Path, content: str) -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def force_full_scan(sync_service: SyncService) -> None:
    """Force next sync to do a full scan by clearing watermark (for testing moves/deletions)."""
    if sync_service.entity_repository.project_id is not None:
        project = await sync_service.project_repository.find_by_id(
            sync_service.entity_repository.project_id
        )
        if project:
            await sync_service.project_repository.update(
                project.id,
                {
                    "last_scan_timestamp": None,
                    "last_file_count": None,
                },
            )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refresh_dataview_relations_after_all_files_synced(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """
    Test that relations are updated after all files synced.
    
    Scenario: Relations updated after all files synced
      Given a milestone with a Dataview query for user-stories
      And 2 user-stories are synced AFTER the milestone
      When refresh_dataview_relations is called
      Then the milestone should have 2 dataview_link relations
    """
    project_dir = project_config.home

    # Create milestone with Dataview query FIRST
    milestone_content = dedent("""
        ---
        title: Milestone 1
        type: milestone
        status: In Progress
        ---
        # Milestone 1
        
        ## User Stories
        
        ```dataview
        TABLE status
        FROM "product-memories"
        WHERE type = "user-story" AND milestone = "Milestone 1"
        ```
    """)
    await create_test_file(project_dir / "milestone-1.md", milestone_content)

    # Initial sync - milestone created but user stories don't exist yet
    await sync_service.sync(project_config.home)

    # Verify milestone exists with no dataview_link relations
    milestone = await entity_service.get_by_permalink("milestone-1")
    assert milestone is not None
    dataview_relations_before = [
        r for r in milestone.relations if r.relation_type == "dataview_link"
    ]
    assert len(dataview_relations_before) == 0, "No user stories exist yet"

    # NOW create the user stories
    us1_content = dedent("""
        ---
        title: US-001 Feature A
        type: user-story
        status: In Progress
        milestone: Milestone 1
        ---
        # US-001 Feature A
        
        User story content
    """)
    us2_content = dedent("""
        ---
        title: US-002 Feature B
        type: user-story
        status: Done
        milestone: Milestone 1
        ---
        # US-002 Feature B
        
        User story content
    """)
    await create_test_file(
        project_dir / "product-memories" / "us-001.md", us1_content
    )
    await create_test_file(
        project_dir / "product-memories" / "us-002.md", us2_content
    )

    # Force full scan to ensure new files are detected
    await force_full_scan(sync_service)

    # Sync the new user stories
    await sync_service.sync(project_config.home)

    # Verify user stories exist
    us1 = await entity_service.get_by_permalink("product-memories/us-001")
    us2 = await entity_service.get_by_permalink("product-memories/us-002")
    assert us1 is not None
    assert us2 is not None

    # Call refresh_dataview_relations
    await sync_service.refresh_dataview_relations()

    # Verify milestone now has 2 dataview_link relations
    milestone = await entity_service.get_by_permalink("milestone-1")
    dataview_relations_after = [
        r for r in milestone.relations if r.relation_type == "dataview_link"
    ]
    assert len(dataview_relations_after) == 2, (
        "Milestone should have 2 dataview_link relations after refresh"
    )

    # Verify the relations point to the correct user stories
    relation_targets = {r.to_name for r in dataview_relations_after}
    assert "US-001 Feature A" in relation_targets
    assert "US-002 Feature B" in relation_targets


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refresh_dataview_relations_removes_stale_links(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """
    Test that relations are removed when note no longer matches.
    
    Scenario: Relations removed when note no longer matches
      Given a milestone with 3 dataview_link relations
      When one user-story status changes to not match the query
      And refresh_dataview_relations is called
      Then the milestone should have 2 dataview_link relations
    """
    project_dir = project_config.home

    # Create milestone with Dataview query for "In Progress" stories
    milestone_content = dedent("""
        ---
        title: Milestone 2
        type: milestone
        status: In Progress
        ---
        # Milestone 2
        
        ## Active User Stories
        
        ```dataview
        TABLE status
        FROM "product-memories"
        WHERE type = "user-story" AND status = "In Progress"
        ```
    """)
    await create_test_file(project_dir / "milestone-2.md", milestone_content)

    # Create 3 user stories, all "In Progress"
    for i in range(1, 4):
        us_content = dedent(f"""
            ---
            title: US-00{i} Story {i}
            type: user-story
            status: In Progress
            ---
            # US-00{i} Story {i}
            
            Content
        """)
        await create_test_file(
            project_dir / "product-memories" / f"us-00{i}.md", us_content
        )

    # Sync all files
    await sync_service.sync(project_config.home)

    # Call refresh_dataview_relations to create initial relations
    await sync_service.refresh_dataview_relations()

    # Verify milestone has 3 dataview_link relations
    milestone = await entity_service.get_by_permalink("milestone-2")
    dataview_relations_initial = [
        r for r in milestone.relations if r.relation_type == "dataview_link"
    ]
    assert len(dataview_relations_initial) == 3, "Should have 3 relations initially"

    # Change one user story status to "Done" (no longer matches query)
    us2_updated = dedent("""
        ---
        title: US-002 Story 2
        type: user-story
        status: Done
        ---
        # US-002 Story 2
        
        Content
    """)
    (project_dir / "product-memories" / "us-002.md").write_text(us2_updated)

    # Sync the modified file
    await sync_service.sync(project_config.home)

    # Call refresh_dataview_relations
    await sync_service.refresh_dataview_relations()

    # Verify milestone now has only 2 dataview_link relations
    milestone = await entity_service.get_by_permalink("milestone-2")
    dataview_relations_after = [
        r for r in milestone.relations if r.relation_type == "dataview_link"
    ]
    assert len(dataview_relations_after) == 2, (
        "Milestone should have 2 relations after one story changed status"
    )

    # Verify the remaining relations are correct
    relation_targets = {r.to_name for r in dataview_relations_after}
    assert "US-001 Story 1" in relation_targets
    assert "US-003 Story 3" in relation_targets
    assert "US-002 Story 2" not in relation_targets, "US-002 should be removed"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refresh_dataview_relations_handles_multiple_queries(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """
    Test that refresh handles notes with multiple Dataview queries.
    
    Scenario: Multiple queries in one note
      Given a milestone with 2 Dataview queries
      When refresh_dataview_relations is called
      Then all discovered links from both queries should be present
    """
    project_dir = project_config.home

    # Create milestone with 2 Dataview queries
    milestone_content = dedent("""
        ---
        title: Milestone 3
        type: milestone
        ---
        # Milestone 3
        
        ## In Progress Stories
        
        ```dataview
        LIST
        FROM "product-memories"
        WHERE type = "user-story" AND status = "In Progress"
        ```
        
        ## Done Stories
        
        ```dataview
        LIST
        FROM "product-memories"
        WHERE type = "user-story" AND status = "Done"
        ```
    """)
    await create_test_file(project_dir / "milestone-3.md", milestone_content)

    # Create user stories with different statuses
    us1_content = dedent("""
        ---
        title: US-101 Active Story
        type: user-story
        status: In Progress
        ---
        # US-101 Active Story
        
        Content
    """)
    us2_content = dedent("""
        ---
        title: US-102 Completed Story
        type: user-story
        status: Done
        ---
        # US-102 Completed Story
        
        Content
    """)
    await create_test_file(
        project_dir / "product-memories" / "us-101.md", us1_content
    )
    await create_test_file(
        project_dir / "product-memories" / "us-102.md", us2_content
    )

    # Sync all files
    await sync_service.sync(project_config.home)

    # Call refresh_dataview_relations
    await sync_service.refresh_dataview_relations()

    # Verify milestone has relations from both queries
    milestone = await entity_service.get_by_permalink("milestone-3")
    dataview_relations = [
        r for r in milestone.relations if r.relation_type == "dataview_link"
    ]
    assert len(dataview_relations) == 2, "Should have links from both queries"

    relation_targets = {r.to_name for r in dataview_relations}
    assert "US-101 Active Story" in relation_targets
    assert "US-102 Completed Story" in relation_targets


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refresh_dataview_relations_no_queries(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """
    Test that refresh handles notes without Dataview queries gracefully.
    
    Scenario: Note without Dataview queries
      Given a note with no Dataview queries
      When refresh_dataview_relations is called
      Then no dataview_link relations should be created
      And no errors should occur
    """
    project_dir = project_config.home

    # Create note without Dataview queries
    note_content = dedent("""
        ---
        title: Regular Note
        type: note
        ---
        # Regular Note
        
        Just a regular note with no Dataview queries.
        
        ## Relations
        - relates_to [[other-note]]
    """)
    await create_test_file(project_dir / "regular-note.md", note_content)

    # Sync
    await sync_service.sync(project_config.home)

    # Call refresh_dataview_relations (should not error)
    await sync_service.refresh_dataview_relations()

    # Verify no dataview_link relations were created
    note = await entity_service.get_by_permalink("regular-note")
    dataview_relations = [
        r for r in note.relations if r.relation_type == "dataview_link"
    ]
    assert len(dataview_relations) == 0, "No dataview_link relations should exist"

    # Verify the regular relation still exists
    regular_relations = [
        r for r in note.relations if r.relation_type == "relates_to"
    ]
    assert len(regular_relations) == 1, "Regular relation should still exist"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refresh_dataview_relations_empty_vault(
    sync_service: SyncService,
    project_config: ProjectConfig,
):
    """
    Test that refresh handles empty vault gracefully.
    
    Scenario: Empty vault
      Given an empty vault with no notes
      When refresh_dataview_relations is called
      Then no errors should occur
    """
    # Call refresh on empty vault (should not error)
    await sync_service.refresh_dataview_relations()

    # No assertions needed - just verify it doesn't crash
