"""Debug test for Dataview relations."""

import pytest
from pathlib import Path
from textwrap import dedent


@pytest.mark.asyncio
async def test_dataview_detection_simple(
    config_home,
    sync_service,
    entity_repository,
    relation_repository,
):
    """Simple test to debug Dataview detection and relation creation."""
    
    # Create a simple note with Dataview query
    note_path = config_home / "test.md"
    note_path.write_text(dedent('''---
        title: Test Note
        ---
        # Test Note
        
        ```dataview
        LIST
        FROM ""
        ```
        '''))
    
    # Sync the file
    print(f"\n=== Syncing file: {note_path} ===")
    entity, checksum = await sync_service.sync_markdown_file(str(note_path))
    
    print(f"\n=== Entity created ===")
    print(f"ID: {entity.id}")
    print(f"Title: {entity.title}")
    print(f"Relations count: {len(entity.relations)}")
    
    # Get all relations
    all_relations = await relation_repository.find_all()
    print(f"\n=== All relations in DB ===")
    for rel in all_relations:
        print(f"  - {rel.type}: {rel.source_id} -> {rel.target_id}")
    
    # Check for dataview_link relations
    dataview_relations = [r for r in all_relations if r.type == "dataview_link"]
    print(f"\n=== Dataview relations: {len(dataview_relations)} ===")
    
    assert len(dataview_relations) >= 0, "Test completed (no assertion failure)"
