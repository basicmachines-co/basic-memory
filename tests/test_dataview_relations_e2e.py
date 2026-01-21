"""E2E test for Dataview relations persistence.

Tests that Dataview queries are detected, executed, and their results
are persisted as relations in the database.
"""

import pytest
from pathlib import Path
from textwrap import dedent


@pytest.mark.asyncio
async def test_dataview_relations_persisted_e2e(
    tmp_path,
    sync_service,
    entity_repository,
    relation_repository,
    config_home,
):
    """
    E2E test: Dataview discovered links should be persisted as relations.
    
    Given:
    - A milestone note with a Dataview query: FROM "stories" WHERE type = "user-story"
    - 2 user-story notes in the stories folder
    
    When:
    - All notes are synced
    
    Then:
    - The milestone should have 2 relations of type "dataview_link"
    - build_context on the milestone should return the 2 user-stories
    """
    # Setup: Create test files in the config_home directory
    stories_dir = config_home / "stories"
    stories_dir.mkdir(parents=True, exist_ok=True)
    
    # Create user stories
    us_001_path = stories_dir / "US-001.md"
    us_001_path.write_text(dedent('''
        ---
        title: US-001 Test Story
        type: user-story
        ---
        # US-001 Test Story
        
        Content here for the first user story.
        ''').strip())
    
    us_002_path = stories_dir / "US-002.md"
    us_002_path.write_text(dedent('''
        ---
        title: US-002 Another Story
        type: user-story
        ---
        # US-002 Another Story
        
        More content for the second user story.
        ''').strip())
    
    # Create milestone with Dataview query
    milestone_path = config_home / "M1.md"
    milestone_path.write_text(dedent('''
        ---
        title: M1 Milestone
        type: milestone
        ---
        # M1 Milestone
        
        ## User Stories
        
        ```dataview
        LIST
        FROM "stories"
        WHERE type = "user-story"
        ```
        ''').strip())
    
    # Sync all files
    await sync_service.sync_markdown_file(str(us_001_path))
    await sync_service.sync_markdown_file(str(us_002_path))
    await sync_service.sync_markdown_file(str(milestone_path))
    
    # Verify entities were created
    all_entities = await entity_repository.find_all()
    entity_titles = {e.title for e in all_entities}
    
    # The title field comes from the frontmatter title
    assert "US-001 Test Story" in entity_titles, f"US-001 Test Story not found. Entities: {entity_titles}"
    assert "US-002 Another Story" in entity_titles, f"US-002 Another Story not found. Entities: {entity_titles}"
    assert "M1 Milestone" in entity_titles, f"M1 Milestone not found. Entities: {entity_titles}"
    
    # Get the milestone entity
    milestones = await entity_repository.get_by_title("M1 Milestone")
    assert len(milestones) > 0, "Milestone entity not found"
    milestone = milestones[0]
    
    # Verify relations exist
    all_relations = await relation_repository.find_all()
    
    # Filter relations from the milestone
    milestone_relations = [r for r in all_relations if r.from_id == milestone.id]
    
    # Check for dataview_link relations
    dataview_relations = [r for r in milestone_relations if r.relation_type == "dataview_link"]
    
    assert len(dataview_relations) == 2, (
        f"Expected 2 dataview_link relations, found {len(dataview_relations)}. "
        f"All milestone relations: {[(r.relation_type, r.to_id) for r in milestone_relations]}"
    )
    
    # Verify the targets are the user stories
    target_ids = {r.to_id for r in dataview_relations}
    
    us_001_list = await entity_repository.get_by_title("US-001 Test Story")
    us_002_list = await entity_repository.get_by_title("US-002 Another Story")
    
    assert len(us_001_list) > 0, "US-001 entity not found"
    assert len(us_002_list) > 0, "US-002 entity not found"
    
    us_001 = us_001_list[0]
    us_002 = us_002_list[0]
    
    assert us_001.id in target_ids, f"US-001 not linked. Target IDs: {target_ids}"
    assert us_002.id in target_ids, f"US-002 not linked. Target IDs: {target_ids}"


@pytest.mark.asyncio
async def test_dataview_relations_with_table_query(
    tmp_path,
    sync_service,
    entity_repository,
    relation_repository,
    config_home,
):
    """
    Test Dataview TABLE query also creates relations.
    
    Given:
    - A project note with a Dataview TABLE query
    - 3 task notes with different statuses
    
    When:
    - All notes are synced
    
    Then:
    - The project should have 3 dataview_link relations (one per task)
    """
    # Setup: Create test files
    tasks_dir = config_home / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    
    # Create tasks
    for i, status in enumerate(["todo", "in-progress", "done"], start=1):
        task_path = tasks_dir / f"task-{i}.md"
        task_path.write_text(dedent(f'''
            ---
            title: Task {i}
            type: task
            status: {status}
            ---
            # Task {i}
            
            Task content.
            ''').strip())
    
    # Create project with TABLE query
    project_path = config_home / "project.md"
    project_path.write_text(dedent('''
        ---
        title: My Project
        type: project
        ---
        # My Project
        
        ## Tasks
        
        ```dataview
        TABLE status
        FROM "tasks"
        WHERE type = "task"
        ```
        ''').strip())
    
    # Sync all files
    await sync_service.sync(config_home)
    
    # Refresh Dataview relations after sync to ensure all entities are indexed
    await sync_service.refresh_dataview_relations()
    
    # Verify project entity (title comes from frontmatter)
    projects = await entity_repository.get_by_title("My Project")
    assert len(projects) > 0, "Project entity not found"
    project = projects[0]
    
    # Verify relations
    all_relations = await relation_repository.find_all()
    project_relations = [r for r in all_relations if r.from_id == project.id]
    dataview_relations = [r for r in project_relations if r.relation_type == "dataview_link"]
    
    assert len(dataview_relations) == 3, (
        f"Expected 3 dataview_link relations, found {len(dataview_relations)}"
    )


@pytest.mark.asyncio
async def test_dataview_relations_update_on_resync(
    tmp_path,
    sync_service,
    entity_repository,
    relation_repository,
    config_home,
):
    """
    Test that Dataview relations are updated when query results change.
    
    Given:
    - A note with a Dataview query
    - 2 matching notes initially
    
    When:
    - A third matching note is added and resynced
    
    Then:
    - The source note should now have 3 dataview_link relations
    """
    # Setup: Create initial files
    notes_dir = config_home / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    
    # Create 2 initial notes
    for i in [1, 2]:
        note_path = notes_dir / f"note-{i}.md"
        note_path.write_text(dedent(f'''
            ---
            title: Note {i}
            type: note
            tag: important
            ---
            # Note {i}
            ''').strip())
    
    # Create index with query
    index_path = config_home / "index.md"
    index_path.write_text(dedent('''
        ---
        title: Index
        type: index
        ---
        # Index
        
        ```dataview
        LIST
        FROM "notes"
        WHERE tag = "important"
        ```
        ''').strip())
    
    # Initial sync
    await sync_service.sync(config_home)
    
    # Refresh Dataview relations after sync to ensure all entities are indexed
    await sync_service.refresh_dataview_relations()
    
    # Verify initial state (title comes from frontmatter)
    indexes = await entity_repository.get_by_title("Index")
    assert len(indexes) > 0, "Index entity not found"
    index = indexes[0]
    
    initial_relations = await relation_repository.find_by_source(index.id)
    initial_dataview = [r for r in initial_relations if r.relation_type == "dataview_link"]
    
    assert len(initial_dataview) == 2, f"Expected 2 initial relations, found {len(initial_dataview)}"
    
    # Add a third note
    note_3_path = notes_dir / "note-3.md"
    note_3_path.write_text(dedent('''
        ---
        title: Note 3
        type: note
        tag: important
        ---
        # Note 3
        ''').strip())
    
    # Resync all files with force_full=True to detect the new note
    await sync_service.sync(config_home, force_full=True)
    
    # Refresh Dataview relations to update the index's links
    await sync_service.refresh_dataview_relations()
    
    # Verify updated state
    updated_relations = await relation_repository.find_by_source(index.id)
    updated_dataview = [r for r in updated_relations if r.relation_type == "dataview_link"]
    
    assert len(updated_dataview) == 3, (
        f"Expected 3 relations after adding note, found {len(updated_dataview)}"
    )
    
    # Verify the new note is linked
    note_3_list = await entity_repository.get_by_title("Note 3")
    assert len(note_3_list) > 0, "Note 3 entity not found"
    note_3 = note_3_list[0]
    
    target_ids = {r.to_id for r in updated_dataview}
    assert note_3.id in target_ids, "Note 3 not linked after resync"
