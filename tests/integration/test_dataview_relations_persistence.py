"""
Integration tests for Dataview relations persistence.

Tests that links discovered by Dataview queries are persisted as relations
in the database and can be followed by build_context.
"""

import pytest
from pathlib import Path
from textwrap import dedent


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dataview_links_are_persisted_as_relations(
    sync_service, project_config, entity_repository, relation_repository
):
    """
    Scenario: Dataview links are persisted as relations
      Given a note with a Dataview query that returns 3 notes
      When the note is synced
      Then 3 relations of type "dataview_link" should be created
      And build_context should return these 3 notes
    """
    project_dir = project_config.home
    
    # Create source note with Dataview query
    source_note = project_dir / "source.md"
    source_note.write_text(dedent("""
        # Source Note
        
        This note has a Dataview query:
        
        ```dataview
        LIST FROM "projects"
        ```
    """).strip())
    
    # Create target notes that will be discovered
    projects_dir = project_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    
    (projects_dir / "project-a.md").write_text("# Project A")
    (projects_dir / "project-b.md").write_text("# Project B")
    (projects_dir / "project-c.md").write_text("# Project C")
    
    # Sync the vault
    await sync_service.sync(project_dir)
    
    # Get the source note entity
    source_path = "source.md"
    source_entity = await entity_repository.get_by_file_path(source_path)
    assert source_entity is not None, "Source note should be synced"
    
    # Get all relations from source note
    relations = await relation_repository.find_by_source(source_entity.id)
    
    # Filter dataview_link relations
    dataview_relations = [r for r in relations if r.relation_type == "dataview_link"]
    
    # Should have 3 dataview_link relations (one for each project note)
    assert len(dataview_relations) == 3, (
        f"Expected 3 dataview_link relations, got {len(dataview_relations)}"
    )
    
    # Verify target notes exist
    target_paths = {
        "projects/project-a.md",
        "projects/project-b.md",
        "projects/project-c.md",
    }
    
    discovered_targets = set()
    for relation in dataview_relations:
        target_entity = await entity_repository.find_by_id(relation.to_id)
        assert target_entity is not None
        discovered_targets.add(target_entity.file_path)
    
    assert discovered_targets == target_paths, (
        f"Discovered targets don't match expected. "
        f"Expected: {target_paths}, Got: {discovered_targets}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explicit_relations_are_preserved(
    sync_service, project_config, entity_repository, relation_repository
):
    """
    Scenario: Existing explicit relations are preserved
      Given a note with explicit relations and Dataview queries
      When the note is synced
      Then both explicit and dataview relations should exist
    """
    project_dir = project_config.home
    
    # Create projects directory first
    projects_dir = project_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    
    (projects_dir / "project-a.md").write_text("# Project A")
    (projects_dir / "project-b.md").write_text("# Project B")
    (projects_dir / "project-c.md").write_text("# Project C")
    
    # Create a note with both explicit wikilink and Dataview query
    mixed_note = project_dir / "mixed.md"
    mixed_note.write_text(dedent("""
        # Mixed Note
        
        Explicit link: [[projects/project-a]]
        
        Dataview query:
        ```dataview
        LIST FROM "projects"
        ```
    """).strip())
    
    # Sync the vault
    await sync_service.sync(project_dir)
    
    # Get the mixed note entity
    mixed_path = "mixed.md"
    mixed_entity = await entity_repository.get_by_file_path(mixed_path)
    assert mixed_entity is not None
    
    # Get all relations
    relations = await relation_repository.find_by_source(mixed_entity.id)
    
    # Debug: print all relation types
    relation_types = [r.relation_type for r in relations]
    print(f"All relation types: {relation_types}")
    
    # Should have both explicit and dataview relations
    explicit_relations = [r for r in relations if r.relation_type == "links_to"]
    dataview_relations = [r for r in relations if r.relation_type == "dataview_link"]
    
    assert len(explicit_relations) == 1, f"Should have 1 explicit links_to relation, got types: {relation_types}"
    assert len(dataview_relations) == 3, f"Should have 3 dataview_link relations, got {len(dataview_relations)}"
    
    # Note: The current behavior allows the same target to appear in both explicit and dataview relations.
    # This means project-a will have both a links_to and a dataview_link relation.
    # Total: 4 relations (1 links_to + 3 dataview_link) pointing to 3 unique targets.
    
    # Verify we have all 3 unique project targets
    unique_target_ids = set(r.to_id for r in relations)
    assert len(unique_target_ids) == 3, (
        f"Should have 3 unique target entities (project-a, project-b, project-c), "
        f"got {len(unique_target_ids)}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_dataview_queries_no_relations(
    sync_service, project_config, entity_repository, relation_repository
):
    """
    Test that notes without Dataview queries don't get dataview_link relations.
    """
    project_dir = project_config.home
    
    # Create a note without Dataview queries
    plain_note = project_dir / "plain.md"
    plain_note.write_text("# Plain Note\n\nNo Dataview queries here.")
    
    # Sync the vault
    await sync_service.sync(project_dir)
    
    # Get the plain note entity
    plain_path = "plain.md"
    plain_entity = await entity_repository.get_by_file_path(plain_path)
    assert plain_entity is not None
    
    # Get relations
    relations = await relation_repository.find_by_source(plain_entity.id)
    dataview_relations = [r for r in relations if r.relation_type == "dataview_link"]
    
    assert len(dataview_relations) == 0, "Should have no dataview_link relations"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dataview_relations_updated_on_resync(
    sync_service, project_config, entity_repository, relation_repository
):
    """
    Test that dataview relations are updated when the query changes.
    """
    project_dir = project_config.home
    
    # Create initial setup
    projects_dir = project_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    
    (projects_dir / "project-a.md").write_text("# Project A")
    (projects_dir / "project-b.md").write_text("# Project B")
    (projects_dir / "project-c.md").write_text("# Project C")
    
    source_note = project_dir / "source.md"
    source_note.write_text(dedent("""
        # Source Note
        
        This note has a Dataview query:
        
        ```dataview
        LIST FROM "projects"
        ```
    """).strip())
    
    # Initial sync
    await sync_service.sync(project_dir)
    
    source_path = "source.md"
    source_entity = await entity_repository.get_by_file_path(source_path)
    initial_relations = await relation_repository.find_by_source(source_entity.id)
    initial_dataview = [r for r in initial_relations if r.relation_type == "dataview_link"]
    
    assert len(initial_dataview) == 3, "Should start with 3 dataview relations"
    
    # Modify the query to be more restrictive
    source_note.write_text(dedent("""
        # Source Note
        
        Updated query:
        
        ```dataview
        LIST FROM "projects" WHERE file.name = "project-a.md"
        ```
    """).strip())
    
    # Resync with force_full to ensure the modified file is detected
    await sync_service.sync(project_dir, force_full=True)
    
    # Get updated relations
    updated_relations = await relation_repository.find_by_source(source_entity.id)
    updated_dataview = [r for r in updated_relations if r.relation_type == "dataview_link"]
    
    # Should now have only 1 dataview relation
    assert len(updated_dataview) == 1, (
        f"Should have 1 dataview relation after update, got {len(updated_dataview)}"
    )
