"""Test general sync behavior."""

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

import pytest

from basic_memory.config import ProjectConfig, BasicMemoryConfig
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository
from basic_memory.schemas.search import SearchQuery
from basic_memory.services import EntityService, FileService
from basic_memory.services.search_service import SearchService
from basic_memory.sync.sync_service import SyncService


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def touch_file(path: Path) -> None:
    """Touch a file to update its mtime (for watermark testing)."""
    import time

    # Read and rewrite to update mtime
    content = path.read_text()
    time.sleep(0.5)  # Ensure mtime changes and is newer than watermark (500ms)
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
async def test_forward_reference_resolution(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """Test that forward references get resolved when target file is created."""
    project_dir = project_config.home

    # First create a file with a forward reference
    source_content = """
---
type: knowledge
---
# Source Document

## Relations
- depends_on [[target-doc]]
- depends_on [[target-doc]] # duplicate
"""
    await create_test_file(project_dir / "source.md", source_content)

    # Initial sync - should create forward reference
    await sync_service.sync(project_config.home)

    # Verify forward reference
    source = await entity_service.get_by_permalink("source")
    assert len(source.relations) == 1
    assert source.relations[0].to_id is None
    assert source.relations[0].to_name == "target-doc"

    # Now create the target file
    target_content = """
---
type: knowledge
---
# Target Doc
Target content
"""
    target_file = project_dir / "target_doc.md"
    await create_test_file(target_file, target_content)

    # Force full scan to ensure the new file is detected
    # Incremental scans have timing precision issues with watermarks on some filesystems
    await force_full_scan(sync_service)

    # Sync again - should resolve the reference
    await sync_service.sync(project_config.home)

    # Verify reference is now resolved
    source = await entity_service.get_by_permalink("source")
    target = await entity_service.get_by_permalink("target-doc")
    assert len(source.relations) == 1
    assert source.relations[0].to_id == target.id
    assert source.relations[0].to_name == target.title


@pytest.mark.asyncio
async def test_resolve_relations_deletes_duplicate_unresolved_relation(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """Test that resolve_relations deletes duplicate unresolved relations on IntegrityError.

    When resolving a forward reference would create a duplicate (from_id, to_id, relation_type),
    the unresolved relation should be deleted since a resolved version already exists.
    """
    from unittest.mock import patch
    from sqlalchemy.exc import IntegrityError
    from basic_memory.models import Relation

    project_dir = project_config.home

    # Create source entity
    source_content = """
---
type: knowledge
---
# Source Entity
Content
"""
    await create_test_file(project_dir / "source.md", source_content)

    # Create target entity
    target_content = """
---
type: knowledge
---
# Target Entity
Content
"""
    await create_test_file(project_dir / "target.md", target_content)

    # Sync to create both entities
    await sync_service.sync(project_config.home)

    source = await entity_service.get_by_permalink("source")
    await entity_service.get_by_permalink("target")

    # Create an unresolved relation that will resolve to target
    unresolved_relation = Relation(
        from_id=source.id,
        to_id=None,  # Unresolved
        to_name="target",  # Will resolve to target entity
        relation_type="relates_to",
    )
    await sync_service.relation_repository.add(unresolved_relation)
    unresolved_id = unresolved_relation.id

    # Verify we have the unresolved relation
    source = await entity_service.get_by_permalink("source")
    assert len(source.outgoing_relations) == 1
    assert source.outgoing_relations[0].to_id is None

    # Mock the repository update to raise IntegrityError (simulating existing duplicate)

    async def mock_update_raises_integrity_error(entity_id, data):
        # Simulate: a resolved relation with same (from_id, to_id, relation_type) already exists
        raise IntegrityError(
            "UNIQUE constraint failed: relation.from_id, relation.to_id, relation.relation_type",
            None,
            None,  # pyright: ignore [reportArgumentType]
        )

    with patch.object(
        sync_service.relation_repository, "update", side_effect=mock_update_raises_integrity_error
    ):
        # Call resolve_relations - should hit IntegrityError and delete the duplicate
        await sync_service.resolve_relations()

    # Verify the unresolved relation was deleted
    deleted = await sync_service.relation_repository.find_by_id(unresolved_id)
    assert deleted is None

    # Verify no unresolved relations remain
    unresolved = await sync_service.relation_repository.find_unresolved_relations()
    assert len(unresolved) == 0


@pytest.mark.asyncio
async def test_sync(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test basic knowledge sync functionality."""
    # Create test files
    project_dir = project_config.home

    # New entity with relation
    new_content = """
---
type: knowledge
permalink: concept/test-concept
created: 2023-01-01
modified: 2023-01-01
---
# Test Concept

A test concept.

## Observations
- [design] Core feature

## Relations
- depends_on [[concept/other]]
"""
    await create_test_file(project_dir / "concept/test_concept.md", new_content)

    # Create related entity in DB that will be deleted
    # because file was not found
    other = Entity(
        permalink="concept/other",
        title="Other",
        entity_type="test",
        file_path="concept/other.md",
        checksum="12345678",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await entity_service.repository.add(other)

    # Run sync
    await sync_service.sync(project_config.home)

    # Verify results
    entities = await entity_service.repository.find_all()
    assert len(entities) == 1

    # Find new entity
    test_concept = next(e for e in entities if e.permalink == "concept/test-concept")
    assert test_concept.entity_type == "knowledge"

    # Verify relation was created
    # with forward link
    entity = await entity_service.get_by_permalink(test_concept.permalink)
    relations = entity.relations
    assert len(relations) == 1, "Expected 1 relation for entity"
    assert relations[0].to_name == "concept/other"


@pytest.mark.asyncio
async def test_sync_hidden_file(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test basic knowledge sync functionality."""
    # Create test files
    project_dir = project_config.home

    # hidden file
    await create_test_file(project_dir / "concept/.hidden.md", "hidden")

    # Run sync
    await sync_service.sync(project_config.home)

    # Verify results
    entities = await entity_service.repository.find_all()
    assert len(entities) == 0


@pytest.mark.asyncio
async def test_sync_entity_with_nonexistent_relations(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test syncing an entity that references nonexistent entities."""
    project_dir = project_config.home

    # Create entity that references entities we haven't created yet
    content = """
---
type: knowledge
permalink: concept/depends-on-future
created: 2024-01-01
modified: 2024-01-01
---
# Test Dependencies

## Observations
- [design] Testing future dependencies

## Relations
- depends_on [[concept/not_created_yet]]
- uses [[concept/also_future]]
"""
    await create_test_file(project_dir / "concept/depends_on_future.md", content)

    # Sync
    await sync_service.sync(project_config.home)

    # Verify entity created but no relations
    entity = await sync_service.entity_service.repository.get_by_permalink(
        "concept/depends-on-future"
    )
    assert entity is not None
    assert len(entity.relations) == 2
    assert entity.relations[0].to_name == "concept/not_created_yet"
    assert entity.relations[1].to_name == "concept/also_future"


@pytest.mark.asyncio
async def test_sync_entity_circular_relations(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test syncing entities with circular dependencies."""
    project_dir = project_config.home

    # Create entity A that depends on B
    content_a = """
---
type: knowledge
permalink: concept/entity-a
created: 2024-01-01
modified: 2024-01-01
---
# Entity A

## Observations
- First entity in circular reference

## Relations
- depends_on [[concept/entity-b]]
"""
    await create_test_file(project_dir / "concept/entity_a.md", content_a)

    # Create entity B that depends on A
    content_b = """
---
type: knowledge
permalink: concept/entity-b
created: 2024-01-01
modified: 2024-01-01
---
# Entity B

## Observations
- Second entity in circular reference

## Relations
- depends_on [[concept/entity-a]]
"""
    await create_test_file(project_dir / "concept/entity_b.md", content_b)

    # Sync
    await sync_service.sync(project_config.home)

    # Verify both entities and their relations
    entity_a = await sync_service.entity_service.repository.get_by_permalink("concept/entity-a")
    entity_b = await sync_service.entity_service.repository.get_by_permalink("concept/entity-b")

    # outgoing relations
    assert len(entity_a.outgoing_relations) == 1
    assert len(entity_b.outgoing_relations) == 1

    # incoming relations
    assert len(entity_a.incoming_relations) == 1
    assert len(entity_b.incoming_relations) == 1

    # all relations
    assert len(entity_a.relations) == 2
    assert len(entity_b.relations) == 2

    # Verify circular reference works
    a_relation = entity_a.outgoing_relations[0]
    assert a_relation.to_id == entity_b.id

    b_relation = entity_b.outgoing_relations[0]
    assert b_relation.to_id == entity_a.id


@pytest.mark.asyncio
async def test_sync_entity_duplicate_relations(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test handling of duplicate relations in an entity."""
    project_dir = project_config.home

    # Create target entity first
    target_content = """
---
type: knowledge
permalink: concept/target
created: 2024-01-01
modified: 2024-01-01
---
# Target Entity

## Observations
- something to observe

"""
    await create_test_file(project_dir / "concept/target.md", target_content)

    # Create entity with duplicate relations
    content = """
---
type: knowledge
permalink: concept/duplicate-relations
created: 2024-01-01
modified: 2024-01-01
---
# Test Duplicates

## Observations
- this has a lot of relations

## Relations
- depends_on [[concept/target]]
- depends_on [[concept/target]]  # Duplicate
- uses [[concept/target]]  # Different relation type
- uses [[concept/target]]  # Duplicate of different type
"""
    await create_test_file(project_dir / "concept/duplicate_relations.md", content)

    # Sync
    await sync_service.sync(project_config.home)

    # Verify duplicates are handled
    entity = await sync_service.entity_service.repository.get_by_permalink(
        "concept/duplicate-relations"
    )

    # Count relations by type
    relation_counts = {}
    for rel in entity.relations:
        relation_counts[rel.relation_type] = relation_counts.get(rel.relation_type, 0) + 1

    # Should only have one of each type
    assert relation_counts["depends_on"] == 1
    assert relation_counts["uses"] == 1


@pytest.mark.asyncio
async def test_sync_entity_with_random_categories(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test handling of random observation categories."""
    project_dir = project_config.home

    content = """
---
type: knowledge
permalink: concept/invalid-category
created: 2024-01-01
modified: 2024-01-01
---
# Test Categories

## Observations
- [random category] This is fine
- [ a space category] Should default to note
- This one is not an observation, should be ignored
- [design] This is valid 
"""
    await create_test_file(project_dir / "concept/invalid_category.md", content)

    # Sync
    await sync_service.sync(project_config.home)

    # Verify observations
    entity = await sync_service.entity_service.repository.get_by_permalink(
        "concept/invalid-category"
    )

    assert len(entity.observations) == 3
    categories = [obs.category for obs in entity.observations]

    # Invalid categories should be converted to default
    assert "random category" in categories
    # Valid categories preserved
    assert "a space category" in categories
    assert "design" in categories


@pytest.mark.skip("sometimes fails")
@pytest.mark.asyncio
async def test_sync_entity_with_order_dependent_relations(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that order of entity syncing doesn't affect relation creation."""
    project_dir = project_config.home

    # Create several interrelated entities
    entities = {
        "a": """
---
type: knowledge
permalink: concept/entity-a
created: 2024-01-01
modified: 2024-01-01
---
# Entity A

## Observations
- depends on b
- depends on c

## Relations
- depends_on [[concept/entity-b]]
- depends_on [[concept/entity-c]]
""",
        "b": """
---
type: knowledge
permalink: concept/entity-b
created: 2024-01-01
modified: 2024-01-01
---
# Entity B

## Observations
- depends on c

## Relations
- depends_on [[concept/entity-c]]
""",
        "c": """
---
type: knowledge
permalink: concept/entity-c
created: 2024-01-01
modified: 2024-01-01
---
# Entity C

## Observations
- depends on a

## Relations
- depends_on [[concept/entity-a]]
""",
    }

    # Create files in different orders and verify results are the same
    for name, content in entities.items():
        await create_test_file(project_dir / f"concept/entity_{name}.md", content)

    # Sync
    await sync_service.sync(project_config.home)

    # Verify all relations are created correctly regardless of order
    entity_a = await sync_service.entity_service.repository.get_by_permalink("concept/entity-a")
    entity_b = await sync_service.entity_service.repository.get_by_permalink("concept/entity-b")
    entity_c = await sync_service.entity_service.repository.get_by_permalink("concept/entity-c")

    # Verify outgoing relations by checking actual targets
    a_outgoing_targets = {rel.to_id for rel in entity_a.outgoing_relations}
    assert entity_b.id in a_outgoing_targets, (
        f"A should depend on B. A's targets: {a_outgoing_targets}, B's ID: {entity_b.id}"
    )
    assert entity_c.id in a_outgoing_targets, (
        f"A should depend on C. A's targets: {a_outgoing_targets}, C's ID: {entity_c.id}"
    )
    assert len(entity_a.outgoing_relations) == 2, "A should have exactly 2 outgoing relations"

    b_outgoing_targets = {rel.to_id for rel in entity_b.outgoing_relations}
    assert entity_c.id in b_outgoing_targets, "B should depend on C"
    assert len(entity_b.outgoing_relations) == 1, "B should have exactly 1 outgoing relation"

    c_outgoing_targets = {rel.to_id for rel in entity_c.outgoing_relations}
    assert entity_a.id in c_outgoing_targets, "C should depend on A"
    assert len(entity_c.outgoing_relations) == 1, "C should have exactly 1 outgoing relation"

    # Verify incoming relations by checking actual sources
    a_incoming_sources = {rel.from_id for rel in entity_a.incoming_relations}
    assert entity_c.id in a_incoming_sources, "A should have incoming relation from C"

    b_incoming_sources = {rel.from_id for rel in entity_b.incoming_relations}
    assert entity_a.id in b_incoming_sources, "B should have incoming relation from A"

    c_incoming_sources = {rel.from_id for rel in entity_c.incoming_relations}
    assert entity_a.id in c_incoming_sources, "C should have incoming relation from A"
    assert entity_b.id in c_incoming_sources, "C should have incoming relation from B"


@pytest.mark.asyncio
async def test_sync_empty_directories(sync_service: SyncService, project_config: ProjectConfig):
    """Test syncing empty directories."""
    await sync_service.sync(project_config.home)

    # Should not raise exceptions for empty dirs
    assert project_config.home.exists()


@pytest.mark.skip("flaky on Windows due to filesystem timing precision")
@pytest.mark.asyncio
async def test_sync_file_modified_during_sync(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test handling of files that change during sync process."""
    # Create initial files
    doc_path = project_config.home / "changing.md"
    await create_test_file(
        doc_path,
        """
---
type: knowledge
id: changing
created: 2024-01-01
modified: 2024-01-01
---
# Knowledge File

## Observations
- This is a test
""",
    )

    # Setup async modification during sync
    async def modify_file():
        await asyncio.sleep(0.1)  # Small delay to ensure sync has started
        doc_path.write_text("Modified during sync")

    # Run sync and modification concurrently
    await asyncio.gather(sync_service.sync(project_config.home), modify_file())

    # Verify final state
    doc = await sync_service.entity_service.repository.get_by_permalink("changing")
    assert doc is not None

    # if we failed in the middle of a sync, the next one should fix it.
    if doc.checksum is None:
        await sync_service.sync(project_config.home)
        doc = await sync_service.entity_service.repository.get_by_permalink("changing")
        assert doc.checksum is not None


@pytest.mark.asyncio
async def test_permalink_formatting(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test that permalinks are properly formatted during sync."""

    # Test cases with different filename formats
    test_files = {
        # filename -> expected permalink
        "my_awesome_feature.md": "my-awesome-feature",
        "MIXED_CASE_NAME.md": "mixed-case-name",
        "spaces and_underscores.md": "spaces-and-underscores",
        "design/model_refactor.md": "design/model-refactor",
        "test/multiple_word_directory/feature_name.md": "test/multiple-word-directory/feature-name",
    }

    # Create test files
    content: str = """
---
type: knowledge
created: 2024-01-01
modified: 2024-01-01
---
# Test File

Testing permalink generation.
"""
    for filename, _ in test_files.items():
        await create_test_file(project_config.home / filename, content)

    # Run sync once after all files are created
    await sync_service.sync(project_config.home)

    # Verify permalinks
    entities = await entity_service.repository.find_all()
    for filename, expected_permalink in test_files.items():
        # Find entity for this file
        entity = next(e for e in entities if e.file_path == filename)
        assert entity.permalink == expected_permalink, (
            f"File {filename} should have permalink {expected_permalink}"
        )


@pytest.mark.asyncio
async def test_handle_entity_deletion(
    test_graph,
    sync_service: SyncService,
    entity_repository: EntityRepository,
    search_service: SearchService,
):
    """Test deletion of entity cleans up search index."""

    root_entity = test_graph["root"]
    # Delete the entity
    await sync_service.handle_delete(root_entity.file_path)

    # Verify entity is gone from db
    assert await entity_repository.get_by_permalink(root_entity.permalink) is None

    # Verify entity is gone from search index
    entity_results = await search_service.search(SearchQuery(text=root_entity.title))
    assert len(entity_results) == 0

    obs_results = await search_service.search(SearchQuery(text="Root note 1"))
    assert len(obs_results) == 0

    # Verify relations from root entity are gone
    # (Postgres stemming would match "connects_to" with "connected_to", so use permalink)
    rel_results = await search_service.search(SearchQuery(permalink=root_entity.permalink))
    assert len(rel_results) == 0


@pytest.mark.asyncio
async def test_sync_preserves_timestamps(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """Test that sync preserves file timestamps and frontmatter dates."""
    project_dir = project_config.home

    # Create a file with explicit frontmatter dates
    frontmatter_content = """
---
type: knowledge
---
# Explicit Dates
Testing frontmatter dates
"""
    await create_test_file(project_dir / "explicit_dates.md", frontmatter_content)

    # Create a file without dates (will use file timestamps)
    file_dates_content = """
---
type: knowledge
---
# File Dates
Testing file timestamps
"""
    file_path = project_dir / "file_dates3.md"
    await create_test_file(file_path, file_dates_content)

    # Run sync
    await sync_service.sync(project_config.home)

    # Check explicit frontmatter dates
    explicit_entity = await entity_service.get_by_permalink("explicit-dates")
    assert explicit_entity.created_at is not None
    assert explicit_entity.updated_at is not None

    # Check file timestamps
    file_entity = await entity_service.get_by_permalink("file-dates3")
    file_stats = file_path.stat()

    # Compare using epoch timestamps to handle timezone differences correctly
    # This ensures we're comparing the actual points in time, not display representations
    entity_created_epoch = file_entity.created_at.timestamp()
    entity_updated_epoch = file_entity.updated_at.timestamp()

    # Allow 2s difference on Windows due to filesystem timing precision
    tolerance = 2 if os.name == "nt" else 1
    assert abs(entity_created_epoch - file_stats.st_ctime) < tolerance
    assert abs(entity_updated_epoch - file_stats.st_mtime) < tolerance  # Allow tolerance difference


@pytest.mark.asyncio
async def test_sync_updates_timestamps_on_file_modification(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """Test that sync updates entity timestamps when files are modified.

    This test specifically validates that when an existing file is modified and re-synced,
    the updated_at timestamp in the database reflects the file's actual modification time,
    not the database operation time. This is critical for accurate temporal ordering in
    search and recent_activity queries.
    """
    project_dir = project_config.home

    # Create initial file
    initial_content = """
---
type: knowledge
---
# Test File
Initial content for timestamp test
"""
    file_path = project_dir / "timestamp_test.md"
    await create_test_file(file_path, initial_content)

    # Initial sync
    await sync_service.sync(project_config.home)

    # Get initial entity and timestamps
    entity_before = await entity_service.get_by_permalink("timestamp-test")
    initial_updated_at = entity_before.updated_at

    # Modify the file content and update mtime to be newer than watermark
    modified_content = """
---
type: knowledge
---
# Test File
Modified content for timestamp test

## Observations
- [test] This was modified
"""
    file_path.write_text(modified_content)

    # Touch file to ensure mtime is newer than watermark
    # This uses our helper which sleeps 500ms and rewrites to guarantee mtime change
    await touch_file(file_path)

    # Get the file's modification time after our changes
    file_stats_after_modification = file_path.stat()

    # Force full scan to ensure the modified file is detected
    # (incremental scans have timing precision issues with watermarks on some filesystems)
    await force_full_scan(sync_service)

    # Re-sync the modified file
    await sync_service.sync(project_config.home)

    # Get entity after re-sync
    entity_after = await entity_service.get_by_permalink("timestamp-test")

    # Verify that updated_at changed
    assert entity_after.updated_at != initial_updated_at, (
        "updated_at should change when file is modified"
    )

    # Verify that updated_at matches the file's modification time, not db operation time
    entity_updated_epoch = entity_after.updated_at.timestamp()
    file_mtime = file_stats_after_modification.st_mtime

    # Allow 2s difference on Windows due to filesystem timing precision
    tolerance = 2 if os.name == "nt" else 1
    assert abs(entity_updated_epoch - file_mtime) < tolerance, (
        f"Entity updated_at ({entity_after.updated_at}) should match file mtime "
        f"({datetime.fromtimestamp(file_mtime)}) within {tolerance}s tolerance"
    )

    # Verify the content was actually updated
    assert len(entity_after.observations) == 1
    assert entity_after.observations[0].content == "This was modified"


@pytest.mark.asyncio
async def test_file_move_updates_search_index(
    sync_service: SyncService,
    project_config: ProjectConfig,
    search_service: SearchService,
):
    """Test that moving a file updates its path in the search index."""
    project_dir = project_config.home

    # Create initial file
    content = """
---
type: knowledge
---
# Test Move
Content for move test
"""
    old_path = project_dir / "old" / "test_move.md"
    old_path.parent.mkdir(parents=True)
    await create_test_file(old_path, content)

    # Initial sync
    await sync_service.sync(project_config.home)

    # Move the file
    new_path = project_dir / "new" / "moved_file.md"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)

    # Force full scan to detect the move
    # (rename doesn't update mtime, so incremental scan won't find it)
    await force_full_scan(sync_service)

    # Second sync should detect the move
    await sync_service.sync(project_config.home)

    # Check search index has updated path
    results = await search_service.search(SearchQuery(text="Content for move test"))
    assert len(results) == 1
    assert results[0].file_path == new_path.relative_to(project_dir).as_posix()


@pytest.mark.asyncio
async def test_sync_null_checksum_cleanup(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """Test handling of entities with null checksums from incomplete syncs."""
    # Create entity with null checksum (simulating incomplete sync)
    entity = Entity(
        permalink="concept/incomplete",
        title="Incomplete",
        entity_type="test",
        file_path="concept/incomplete.md",
        checksum=None,  # Null checksum
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await entity_service.repository.add(entity)

    # Create corresponding file
    content = """
---
type: knowledge
id: concept/incomplete
created: 2024-01-01
modified: 2024-01-01
---
# Incomplete Entity

## Observations
- Testing cleanup
"""
    await create_test_file(project_config.home / "concept/incomplete.md", content)

    # Run sync
    await sync_service.sync(project_config.home)

    # Verify entity was properly synced
    updated = await entity_service.get_by_permalink("concept/incomplete")
    assert updated.checksum is not None


@pytest.mark.asyncio
async def test_sync_permalink_resolved(
    sync_service: SyncService, project_config: ProjectConfig, file_service: FileService, app_config
):
    """Test that we resolve duplicate permalinks on sync ."""
    project_dir = project_config.home

    # Create initial file
    content = """
---
type: knowledge
---
# Test Move
Content for move test
"""
    old_path = project_dir / "old" / "test_move.md"
    old_path.parent.mkdir(parents=True)
    await create_test_file(old_path, content)

    # Initial sync
    await sync_service.sync(project_config.home)

    # Move the file
    new_path = project_dir / "new" / "moved_file.md"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)

    # Force full scan to detect the move
    # (rename doesn't update mtime, so incremental scan won't find it)
    await force_full_scan(sync_service)

    # Sync again
    await sync_service.sync(project_config.home)

    file_content, _ = await file_service.read_file(new_path)
    assert "permalink: new/moved-file" in file_content

    # Create another that has the same permalink
    content = """
---
type: knowledge
permalink: new/moved-file
---
# Test Move
Content for move test
"""
    old_path = project_dir / "old" / "test_move.md"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    await create_test_file(old_path, content)

    # Force full scan to detect the new file
    # (file just created may not be newer than watermark due to timing precision)
    await force_full_scan(sync_service)

    # Sync new file
    await sync_service.sync(project_config.home)

    # assert permalink is unique
    file_content, _ = await file_service.read_file(old_path)
    assert "permalink: new/moved-file-1" in file_content


@pytest.mark.asyncio
async def test_sync_permalink_resolved_on_update(
    sync_service: SyncService,
    project_config: ProjectConfig,
    file_service: FileService,
):
    """Test that sync resolves permalink conflicts on update."""
    project_dir = project_config.home

    one_file = project_dir / "one.md"
    two_file = project_dir / "two.md"
    await create_test_file(
        one_file,
        content=dedent(
            """
            ---
            permalink: one
            ---
            test content
            """
        ),
    )
    await create_test_file(
        two_file,
        content=dedent(
            """
            ---
            permalink: two
            ---
            test content
            """
        ),
    )

    # Run sync
    await sync_service.sync(project_config.home)

    # Check permalinks
    file_one_content, _ = await file_service.read_file(one_file)
    assert "permalink: one" in file_one_content

    file_two_content, _ = await file_service.read_file(two_file)
    assert "permalink: two" in file_two_content

    # update the second file with a duplicate permalink
    updated_content = """
---
title: two.md
type: note
permalink: one
tags: []
---

test content
"""
    two_file.write_text(updated_content)

    # Force full scan to detect the modified file
    # (file just modified may not be newer than watermark due to timing precision)
    await force_full_scan(sync_service)

    # Run sync
    await sync_service.sync(project_config.home)

    # Check permalinks
    file_two_content, _ = await file_service.read_file(two_file)
    assert "permalink: two" in file_two_content

    # new content with duplicate permalink
    new_content = """
---
title: new.md
type: note
permalink: one
tags: []
---

test content
"""
    new_file = project_dir / "new.md"
    await create_test_file(new_file, new_content)

    # Force full scan to detect the new file
    # (file just created may not be newer than watermark due to timing precision)
    await force_full_scan(sync_service)

    # Run another time
    await sync_service.sync(project_config.home)

    # Should have deduplicated permalink
    new_file_content, _ = await file_service.read_file(new_file)
    assert "permalink: one-1" in new_file_content


@pytest.mark.asyncio
async def test_sync_permalink_not_created_if_no_frontmatter(
    sync_service: SyncService,
    project_config: ProjectConfig,
    file_service: FileService,
):
    """Test that sync resolves permalink conflicts on update."""
    project_dir = project_config.home

    file = project_dir / "one.md"
    await create_test_file(file)

    # Run sync
    await sync_service.sync(project_config.home)

    # Check permalink not created
    file_content, _ = await file_service.read_file(file)
    assert "permalink:" not in file_content


@pytest.fixture
def test_config_update_permamlinks_on_move(app_config) -> BasicMemoryConfig:
    """Test configuration using in-memory DB."""
    app_config.update_permalinks_on_move = True
    return app_config


@pytest.mark.asyncio
async def test_sync_permalink_updated_on_move(
    test_config_update_permamlinks_on_move: BasicMemoryConfig,
    project_config: ProjectConfig,
    sync_service: SyncService,
    file_service: FileService,
):
    """Test that we update a permalink on a file move if set in config ."""
    project_dir = project_config.home

    # Create initial file
    content = dedent(
        """
        ---
        type: knowledge
        ---
        # Test Move
        Content for move test
        """
    )

    old_path = project_dir / "old" / "test_move.md"
    old_path.parent.mkdir(parents=True)
    await create_test_file(old_path, content)

    # Initial sync
    await sync_service.sync(project_config.home)

    # verify permalink
    old_content, _ = await file_service.read_file(old_path)
    assert "permalink: old/test-move" in old_content

    # Move the file
    new_path = project_dir / "new" / "moved_file.md"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)

    # Force full scan to detect the move
    # (rename doesn't update mtime, so incremental scan won't find it)
    await force_full_scan(sync_service)

    # Sync again
    await sync_service.sync(project_config.home)

    file_content, _ = await file_service.read_file(new_path)
    assert "permalink: new/moved-file" in file_content


@pytest.mark.asyncio
async def test_sync_non_markdown_files(sync_service, project_config, test_files):
    """Test syncing non-markdown files."""
    report = await sync_service.sync(project_config.home)
    assert report.total == 2

    # Check files were detected
    assert test_files["pdf"].name in [f for f in report.new]
    assert test_files["image"].name in [f for f in report.new]

    # Verify entities were created
    pdf_entity = await sync_service.entity_repository.get_by_file_path(str(test_files["pdf"].name))
    assert pdf_entity is not None, "PDF entity should have been created"
    assert pdf_entity.content_type == "application/pdf"

    image_entity = await sync_service.entity_repository.get_by_file_path(
        str(test_files["image"].name)
    )
    assert image_entity.content_type == "image/png"


@pytest.mark.asyncio
async def test_sync_non_markdown_files_modified(
    sync_service, project_config, test_files, file_service
):
    """Test syncing non-markdown files."""
    report = await sync_service.sync(project_config.home)
    assert report.total == 2

    # Check files were detected
    assert test_files["pdf"].name in [f for f in report.new]
    assert test_files["image"].name in [f for f in report.new]

    test_files["pdf"].write_text("New content")
    test_files["image"].write_text("New content")

    # Force full scan to detect the modified files
    # (files just modified may not be newer than watermark due to timing precision)
    await force_full_scan(sync_service)

    report = await sync_service.sync(project_config.home)
    assert len(report.modified) == 2

    pdf_file_content, pdf_checksum = await file_service.read_file(test_files["pdf"].name)
    image_file_content, img_checksum = await file_service.read_file(test_files["image"].name)

    pdf_entity = await sync_service.entity_repository.get_by_file_path(str(test_files["pdf"].name))
    image_entity = await sync_service.entity_repository.get_by_file_path(
        str(test_files["image"].name)
    )

    assert pdf_entity.checksum == pdf_checksum
    assert image_entity.checksum == img_checksum


@pytest.mark.asyncio
async def test_sync_non_markdown_files_move(sync_service, project_config, test_files):
    """Test syncing non-markdown files updates permalink"""
    report = await sync_service.sync(project_config.home)
    assert report.total == 2

    # Check files were detected
    assert test_files["pdf"].name in [f for f in report.new]
    assert test_files["image"].name in [f for f in report.new]

    test_files["pdf"].rename(project_config.home / "moved_pdf.pdf")

    # Force full scan to detect the move
    # (rename doesn't update mtime, so incremental scan won't find it)
    await force_full_scan(sync_service)

    report2 = await sync_service.sync(project_config.home)
    assert len(report2.moves) == 1

    # Verify entity is updated
    pdf_entity = await sync_service.entity_repository.get_by_file_path("moved_pdf.pdf")
    assert pdf_entity is not None
    assert pdf_entity.permalink is None


@pytest.mark.asyncio
async def test_sync_non_markdown_files_deleted(sync_service, project_config, test_files):
    """Test syncing non-markdown files updates permalink"""
    report = await sync_service.sync(project_config.home)
    assert report.total == 2

    # Check files were detected
    assert test_files["pdf"].name in [f for f in report.new]
    assert test_files["image"].name in [f for f in report.new]

    test_files["pdf"].unlink()
    report2 = await sync_service.sync(project_config.home)
    assert len(report2.deleted) == 1

    # Verify entity is deleted
    pdf_entity = await sync_service.entity_repository.get_by_file_path("moved_pdf.pdf")
    assert pdf_entity is None


@pytest.mark.asyncio
async def test_sync_non_markdown_files_move_with_delete(
    sync_service, project_config, test_files, file_service
):
    """Test syncing non-markdown files handles file deletes and renames during sync"""

    # Create initial files
    await create_test_file(project_config.home / "doc.pdf", "content1")
    await create_test_file(project_config.home / "other/doc-1.pdf", "content2")

    # Initial sync
    await sync_service.sync(project_config.home)

    # First move/delete the original file to make way for the move
    (project_config.home / "doc.pdf").unlink()
    (project_config.home / "other/doc-1.pdf").rename(project_config.home / "doc.pdf")

    # Sync again
    await sync_service.sync(project_config.home)

    # Verify the changes
    moved_entity = await sync_service.entity_repository.get_by_file_path("doc.pdf")
    assert moved_entity is not None
    assert moved_entity.permalink is None

    file_content, _ = await file_service.read_file("doc.pdf")
    assert "content2" in file_content


@pytest.mark.asyncio
async def test_sync_relation_to_non_markdown_file(
    sync_service: SyncService, project_config: ProjectConfig, file_service: FileService, test_files
):
    """Test that sync resolves permalink conflicts on update."""
    project_dir = project_config.home

    content = f"""
---
title: a note
type: note
tags: []
---

- relates_to [[{test_files["pdf"].name}]]
"""

    note_file = project_dir / "note.md"
    await create_test_file(note_file, content)

    # Run sync
    await sync_service.sync(project_config.home)

    # Check permalinks
    file_one_content, _ = await file_service.read_file(note_file)
    assert (
        f"""---
title: a note
type: note
tags: []
permalink: note
---

- relates_to [[{test_files["pdf"].name}]]
""".strip()
        == file_one_content
    )


@pytest.mark.asyncio
async def test_sync_regular_file_race_condition_handling(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that sync_regular_file handles race condition with IntegrityError (lines 380-401)."""
    from unittest.mock import patch
    from sqlalchemy.exc import IntegrityError
    from datetime import datetime, timezone

    # Create a test file
    test_file = project_config.home / "test_race.md"
    test_content = """
---
type: knowledge
---
# Test Race Condition
This is a test file for race condition handling.
"""
    await create_test_file(test_file, test_content)

    # Mock the entity_repository.add to raise IntegrityError on first call
    original_add = sync_service.entity_repository.add

    call_count = 0

    async def mock_add(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Simulate race condition - another process created the entity
            raise IntegrityError("UNIQUE constraint failed: entity.file_path", None, None)  # pyright: ignore [reportArgumentType]
        else:
            return await original_add(*args, **kwargs)

    # Mock get_by_file_path to return an existing entity (simulating the race condition result)
    async def mock_get_by_file_path(file_path):
        from basic_memory.models import Entity

        return Entity(
            id=1,
            title="Test Race Condition",
            entity_type="knowledge",
            file_path=str(file_path),
            permalink="test-race-condition",
            content_type="text/markdown",
            checksum="old_checksum",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    # Mock update to return the updated entity
    async def mock_update(entity_id, updates):
        from basic_memory.models import Entity

        return Entity(
            id=entity_id,
            title="Test Race Condition",
            entity_type="knowledge",
            file_path=updates["file_path"],
            permalink="test-race-condition",
            content_type="text/markdown",
            checksum=updates["checksum"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    with (
        patch.object(sync_service.entity_repository, "add", side_effect=mock_add),
        patch.object(
            sync_service.entity_repository, "get_by_file_path", side_effect=mock_get_by_file_path
        ) as mock_get,
        patch.object(
            sync_service.entity_repository, "update", side_effect=mock_update
        ) as mock_update_call,
    ):
        # Call sync_regular_file
        entity, checksum = await sync_service.sync_regular_file(
            str(test_file.relative_to(project_config.home)), new=True
        )

        # Verify it handled the race condition gracefully
        assert entity is not None
        assert entity.title == "Test Race Condition"
        assert entity.file_path == str(test_file.relative_to(project_config.home))

        # Verify that get_by_file_path and update were called as fallback
        assert mock_get.call_count >= 1  # May be called multiple times
        mock_update_call.assert_called_once()


@pytest.mark.asyncio
async def test_sync_regular_file_integrity_error_reraise(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that sync_regular_file re-raises IntegrityError for non-race-condition cases."""
    from unittest.mock import patch
    from sqlalchemy.exc import IntegrityError

    # Create a test file
    test_file = project_config.home / "test_integrity.md"
    test_content = """
---
type: knowledge
---
# Test Integrity Error
This is a test file for integrity error handling.
"""
    await create_test_file(test_file, test_content)

    # Mock the entity_repository.add to raise a different IntegrityError (not file_path constraint)
    async def mock_add(*args, **kwargs):
        # Simulate a different constraint violation
        raise IntegrityError("UNIQUE constraint failed: entity.some_other_field", None, None)  # pyright: ignore [reportArgumentType]

    with patch.object(sync_service.entity_repository, "add", side_effect=mock_add):
        # Should re-raise the IntegrityError since it's not a file_path constraint
        with pytest.raises(
            IntegrityError, match="UNIQUE constraint failed: entity.some_other_field"
        ):
            await sync_service.sync_regular_file(
                str(test_file.relative_to(project_config.home)), new=True
            )


@pytest.mark.asyncio
async def test_sync_regular_file_race_condition_entity_not_found(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test handling when entity is not found after IntegrityError (pragma: no cover case)."""
    from unittest.mock import patch
    from sqlalchemy.exc import IntegrityError

    # Create a test file
    test_file = project_config.home / "test_not_found.md"
    test_content = """
---
type: knowledge
---
# Test Not Found
This is a test file for entity not found after constraint violation.
"""
    await create_test_file(test_file, test_content)

    # Mock the entity_repository.add to raise IntegrityError
    async def mock_add(*args, **kwargs):
        raise IntegrityError("UNIQUE constraint failed: entity.file_path", None, None)  # pyright: ignore [reportArgumentType]

    # Mock get_by_file_path to return None (entity not found)
    async def mock_get_by_file_path(file_path):
        return None

    with (
        patch.object(sync_service.entity_repository, "add", side_effect=mock_add),
        patch.object(
            sync_service.entity_repository, "get_by_file_path", side_effect=mock_get_by_file_path
        ),
    ):
        # Should raise ValueError when entity is not found after constraint violation
        with pytest.raises(ValueError, match="Entity not found after constraint violation"):
            await sync_service.sync_regular_file(
                str(test_file.relative_to(project_config.home)), new=True
            )


@pytest.mark.asyncio
async def test_sync_regular_file_race_condition_update_failed(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test handling when update fails after IntegrityError (pragma: no cover case)."""
    from unittest.mock import patch
    from sqlalchemy.exc import IntegrityError
    from datetime import datetime, timezone

    # Create a test file
    test_file = project_config.home / "test_update_fail.md"
    test_content = """
---
type: knowledge
---
# Test Update Fail
This is a test file for update failure after constraint violation.
"""
    await create_test_file(test_file, test_content)

    # Mock the entity_repository.add to raise IntegrityError
    async def mock_add(*args, **kwargs):
        raise IntegrityError("UNIQUE constraint failed: entity.file_path", None, None)  # pyright: ignore [reportArgumentType]

    # Mock get_by_file_path to return an existing entity
    async def mock_get_by_file_path(file_path):
        from basic_memory.models import Entity

        return Entity(
            id=1,
            title="Test Update Fail",
            entity_type="knowledge",
            file_path=str(file_path),
            permalink="test-update-fail",
            content_type="text/markdown",
            checksum="old_checksum",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    # Mock update to return None (failure)
    async def mock_update(entity_id, updates):
        return None

    with (
        patch.object(sync_service.entity_repository, "add", side_effect=mock_add),
        patch.object(
            sync_service.entity_repository, "get_by_file_path", side_effect=mock_get_by_file_path
        ),
        patch.object(sync_service.entity_repository, "update", side_effect=mock_update),
    ):
        # Should raise ValueError when update fails
        with pytest.raises(ValueError, match="Failed to update entity with ID"):
            await sync_service.sync_regular_file(
                str(test_file.relative_to(project_config.home)), new=True
            )


@pytest.mark.asyncio
async def test_circuit_breaker_skips_after_three_failures(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that circuit breaker skips file after 3 consecutive failures."""
    from unittest.mock import patch

    project_dir = project_config.home
    test_file = project_dir / "failing_file.md"

    # Create a file with malformed content that will fail to parse
    await create_test_file(test_file, "invalid markdown content")

    # Mock sync_markdown_file to always fail
    async def mock_sync_markdown_file(*args, **kwargs):
        raise ValueError("Simulated sync failure")

    with patch.object(sync_service, "sync_markdown_file", side_effect=mock_sync_markdown_file):
        # First sync - should fail and record (1/3)
        report1 = await sync_service.sync(project_dir)
        assert len(report1.skipped_files) == 0  # Not skipped yet

        # Touch file to trigger incremental scan
        await touch_file(test_file)

        # Force full scan to ensure file is detected
        # (touch may not update mtime sufficiently on all filesystems)
        await force_full_scan(sync_service)

        # Second sync - should fail and record (2/3)
        report2 = await sync_service.sync(project_dir)
        assert len(report2.skipped_files) == 0  # Still not skipped

        # Touch file to trigger incremental scan
        await touch_file(test_file)

        # Force full scan to ensure file is detected
        # (touch may not update mtime sufficiently on all filesystems)
        await force_full_scan(sync_service)

        # Third sync - should fail, record (3/3), and be added to skipped list
        report3 = await sync_service.sync(project_dir)
        assert len(report3.skipped_files) == 1
        assert report3.skipped_files[0].path == "failing_file.md"
        assert report3.skipped_files[0].failure_count == 3
        assert "Simulated sync failure" in report3.skipped_files[0].reason

        # Touch file to trigger incremental scan
        await touch_file(test_file)

        # Force full scan to ensure file is detected
        # (touch may not update mtime sufficiently on all filesystems)
        await force_full_scan(sync_service)

        # Fourth sync - should be skipped immediately without attempting
        report4 = await sync_service.sync(project_dir)
        assert len(report4.skipped_files) == 1  # Still skipped


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_file_change(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test that circuit breaker resets when file content changes."""
    from unittest.mock import patch

    project_dir = project_config.home
    test_file = project_dir / "changing_file.md"

    # Create initial failing content
    await create_test_file(test_file, "initial bad content")

    # Mock sync_markdown_file to fail
    call_count = 0

    async def mock_sync_markdown_file(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise ValueError("Simulated sync failure")

    with patch.object(sync_service, "sync_markdown_file", side_effect=mock_sync_markdown_file):
        # Fail 3 times to hit circuit breaker threshold
        await sync_service.sync(project_dir)  # Fail 1
        await touch_file(test_file)  # Touch to trigger incremental scan

        # Force full scan to ensure file is detected
        # (touch may not update mtime sufficiently on all filesystems)
        await force_full_scan(sync_service)

        await sync_service.sync(project_dir)  # Fail 2
        await touch_file(test_file)  # Touch to trigger incremental scan

        # Force full scan to ensure file is detected
        # (touch may not update mtime sufficiently on all filesystems)
        await force_full_scan(sync_service)

        report3 = await sync_service.sync(project_dir)  # Fail 3 - now skipped
        assert len(report3.skipped_files) == 1

    # Now change the file content
    valid_content = dedent(
        """
        ---
        title: Fixed Content
        type: knowledge
        ---
        # Fixed Content
        This should work now.
        """
    ).strip()
    await create_test_file(test_file, valid_content)

    # Force full scan to detect the modified file
    # (file just modified may not be newer than watermark due to timing precision)
    await force_full_scan(sync_service)

    # Circuit breaker should reset and allow retry
    report = await sync_service.sync(project_dir)
    assert len(report.skipped_files) == 0  # Should not be skipped anymore

    # Verify entity was created successfully
    entity = await entity_service.get_by_permalink("changing-file")
    assert entity is not None
    assert entity.title == "Fixed Content"


@pytest.mark.asyncio
async def test_circuit_breaker_clears_on_success(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test that circuit breaker clears failure history after successful sync."""
    from unittest.mock import patch

    project_dir = project_config.home
    test_file = project_dir / "sometimes_failing.md"

    valid_content = dedent(
        """
        ---
        title: Test File
        type: knowledge
        ---
        # Test File
        Test content
        """
    ).strip()
    await create_test_file(test_file, valid_content)

    # Mock to fail twice, then succeed
    call_count = 0
    original_sync_markdown_file = sync_service.sync_markdown_file

    async def mock_sync_markdown_file(path, new):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ValueError("Temporary failure")
        # On third call, use the real implementation
        return await original_sync_markdown_file(path, new)

    # Patch and fail twice
    with patch.object(sync_service, "sync_markdown_file", side_effect=mock_sync_markdown_file):
        await sync_service.sync(project_dir)  # Fail 1
        await touch_file(test_file)  # Touch to trigger incremental scan

        # Force full scan to ensure file is detected
        # (touch may not update mtime sufficiently on all filesystems)
        await force_full_scan(sync_service)

        await sync_service.sync(project_dir)  # Fail 2
        await touch_file(test_file)  # Touch to trigger incremental scan

        # Force full scan to ensure file is detected
        # (touch may not update mtime sufficiently on all filesystems)
        await force_full_scan(sync_service)

        await sync_service.sync(project_dir)  # Succeed

    # Verify failure history was cleared
    assert "sometimes_failing.md" not in sync_service._file_failures

    # Verify entity was created
    entity = await entity_service.get_by_permalink("sometimes-failing")
    assert entity is not None


@pytest.mark.asyncio
async def test_circuit_breaker_handles_checksum_computation_failure(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test circuit breaker behavior when checksum computation fails."""
    from unittest.mock import patch

    project_dir = project_config.home
    test_file = project_dir / "checksum_fail.md"
    await create_test_file(test_file, "content")

    # Mock sync_markdown_file to fail
    async def mock_sync_markdown_file(*args, **kwargs):
        raise ValueError("Sync failure")

    # Mock checksum computation to fail only during _record_failure (not during scan)
    original_compute_checksum = sync_service.file_service.compute_checksum
    call_count = 0

    async def mock_compute_checksum(path):
        nonlocal call_count
        call_count += 1
        # First call is during scan - let it succeed
        if call_count == 1:
            return await original_compute_checksum(path)
        # Second call is during _record_failure - make it fail
        raise IOError("Cannot read file")

    with (
        patch.object(sync_service, "sync_markdown_file", side_effect=mock_sync_markdown_file),
        patch.object(
            sync_service.file_service,
            "compute_checksum",
            side_effect=mock_compute_checksum,
        ),
    ):
        # Should still record failure even if checksum fails
        await sync_service.sync(project_dir)

        # Check that failure was recorded with empty checksum
        assert "checksum_fail.md" in sync_service._file_failures
        failure_info = sync_service._file_failures["checksum_fail.md"]
        assert failure_info.count == 1
        assert failure_info.last_checksum == ""  # Empty when checksum fails


@pytest.mark.asyncio
async def test_sync_fatal_error_terminates_sync_immediately(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test that SyncFatalError terminates sync immediately without circuit breaker retry.

    This tests the fix for issue #188 where project deletion during sync should
    terminate immediately rather than retrying each file 3 times.
    """
    from unittest.mock import patch
    from basic_memory.services.exceptions import SyncFatalError

    project_dir = project_config.home

    # Create multiple test files
    await create_test_file(
        project_dir / "file1.md",
        dedent(
            """
            ---
            type: knowledge
            ---
            # File 1
            Content 1
            """
        ),
    )
    await create_test_file(
        project_dir / "file2.md",
        dedent(
            """
            ---
            type: knowledge
            ---
            # File 2
            Content 2
            """
        ),
    )
    await create_test_file(
        project_dir / "file3.md",
        dedent(
            """
            ---
            type: knowledge
            ---
            # File 3
            Content 3
            """
        ),
    )

    # Mock entity_service.create_entity_from_markdown to raise SyncFatalError on first file
    # This simulates project being deleted during sync
    async def mock_create_entity_from_markdown(*args, **kwargs):
        raise SyncFatalError(
            "Cannot sync file 'file1.md': project_id=99999 does not exist in database. "
            "The project may have been deleted. This sync will be terminated."
        )

    with patch.object(
        entity_service, "create_entity_from_markdown", side_effect=mock_create_entity_from_markdown
    ):
        # Sync should raise SyncFatalError and terminate immediately
        with pytest.raises(SyncFatalError, match="project_id=99999 does not exist"):
            await sync_service.sync(project_dir)

    # Verify that circuit breaker did NOT record this as a file-level failure
    # (SyncFatalError should bypass circuit breaker and re-raise immediately)
    assert "file1.md" not in sync_service._file_failures

    # Verify that no other files were attempted (sync terminated on first error)
    # If circuit breaker was used, we'd see file1 in failures
    # If sync continued, we'd see attempts for file2 and file3


@pytest.mark.asyncio
async def test_scan_directory_basic(sync_service: SyncService, project_config: ProjectConfig):
    """Test basic streaming directory scan functionality."""
    project_dir = project_config.home

    # Create test files in different directories
    await create_test_file(project_dir / "root.md", "root content")
    await create_test_file(project_dir / "subdir/file1.md", "file 1 content")
    await create_test_file(project_dir / "subdir/file2.md", "file 2 content")
    await create_test_file(project_dir / "subdir/nested/file3.md", "file 3 content")

    # Collect results from streaming iterator
    results = []
    async for file_path, stat_info in sync_service.scan_directory(project_dir):
        rel_path = Path(file_path).relative_to(project_dir).as_posix()
        results.append((rel_path, stat_info))

    # Verify all files were found
    file_paths = {rel_path for rel_path, _ in results}
    assert "root.md" in file_paths
    assert "subdir/file1.md" in file_paths
    assert "subdir/file2.md" in file_paths
    assert "subdir/nested/file3.md" in file_paths
    assert len(file_paths) == 4

    # Verify stat info is present for each file
    for rel_path, stat_info in results:
        assert stat_info is not None
        assert stat_info.st_size > 0  # Files have content
        assert stat_info.st_mtime > 0  # Have modification time


@pytest.mark.asyncio
async def test_scan_directory_respects_ignore_patterns(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that streaming scan respects .gitignore patterns."""
    project_dir = project_config.home

    # Create .gitignore file in project (will be used along with .bmignore)
    (project_dir / ".gitignore").write_text("*.ignored\n.hidden/\n")

    # Reload ignore patterns using project's .gitignore
    from basic_memory.ignore_utils import load_gitignore_patterns

    sync_service._ignore_patterns = load_gitignore_patterns(project_dir)

    # Create test files - some should be ignored
    await create_test_file(project_dir / "included.md", "included")
    await create_test_file(project_dir / "excluded.ignored", "excluded")
    await create_test_file(project_dir / ".hidden/secret.md", "secret")
    await create_test_file(project_dir / "subdir/file.md", "file")

    # Collect results
    results = []
    async for file_path, stat_info in sync_service.scan_directory(project_dir):
        rel_path = Path(file_path).relative_to(project_dir).as_posix()
        results.append(rel_path)

    # Verify ignored files were not returned
    assert "included.md" in results
    assert "subdir/file.md" in results
    assert "excluded.ignored" not in results
    assert ".hidden/secret.md" not in results
    assert ".bmignore" not in results  # .bmignore itself should be ignored


@pytest.mark.asyncio
async def test_scan_directory_cached_stat_info(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that streaming scan provides cached stat info (no redundant stat calls)."""
    project_dir = project_config.home

    # Create test file
    test_file = project_dir / "test.md"
    await create_test_file(test_file, "test content")

    # Get stat info from streaming scan
    async for file_path, stat_info in sync_service.scan_directory(project_dir):
        if Path(file_path).name == "test.md":
            # Get independent stat for comparison
            independent_stat = test_file.stat()

            # Verify stat info matches (cached stat should be accurate)
            assert stat_info.st_size == independent_stat.st_size
            assert abs(stat_info.st_mtime - independent_stat.st_mtime) < 1  # Allow 1s tolerance
            assert abs(stat_info.st_ctime - independent_stat.st_ctime) < 1
            break


@pytest.mark.asyncio
async def test_scan_directory_empty_directory(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test streaming scan on empty directory (ignoring hidden files)."""
    project_dir = project_config.home

    # Directory exists but has no user files (may have .basic-memory config dir)
    assert project_dir.exists()

    # Don't create any user files - just scan empty directory
    # Scan should yield no results (hidden files are ignored by default)
    results = []
    async for file_path, stat_info in sync_service.scan_directory(project_dir):
        results.append(file_path)

    # Should find no files (config dirs are hidden and ignored)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_scan_directory_handles_permission_error(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that streaming scan handles permission errors gracefully."""
    import sys

    # Skip on Windows - permission handling is different
    if sys.platform == "win32":
        pytest.skip("Permission tests not reliable on Windows")

    project_dir = project_config.home

    # Create accessible file
    await create_test_file(project_dir / "accessible.md", "accessible")

    # Create restricted directory
    restricted_dir = project_dir / "restricted"
    restricted_dir.mkdir()
    await create_test_file(restricted_dir / "secret.md", "secret")

    # Remove read permission from restricted directory
    restricted_dir.chmod(0o000)

    try:
        # Scan should handle permission error and continue
        results = []
        async for file_path, stat_info in sync_service.scan_directory(project_dir):
            rel_path = Path(file_path).relative_to(project_dir).as_posix()
            results.append(rel_path)

        # Should have found accessible file but not restricted one
        assert "accessible.md" in results
        assert "restricted/secret.md" not in results

    finally:
        # Restore permissions for cleanup
        restricted_dir.chmod(0o755)


@pytest.mark.asyncio
async def test_scan_directory_non_markdown_files(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that streaming scan finds all file types, not just markdown."""
    project_dir = project_config.home

    # Create various file types
    await create_test_file(project_dir / "doc.md", "markdown")
    (project_dir / "image.png").write_bytes(b"PNG content")
    (project_dir / "data.json").write_text('{"key": "value"}')
    (project_dir / "script.py").write_text("print('hello')")

    # Collect results
    results = []
    async for file_path, stat_info in sync_service.scan_directory(project_dir):
        rel_path = Path(file_path).relative_to(project_dir).as_posix()
        results.append(rel_path)

    # All files should be found
    assert "doc.md" in results
    assert "image.png" in results
    assert "data.json" in results
    assert "script.py" in results


@pytest.mark.asyncio
async def test_file_service_checksum_correctness(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that FileService computes correct checksums."""
    import hashlib

    project_dir = project_config.home

    # Test small markdown file
    small_content = "Test content for checksum validation" * 10
    small_file = project_dir / "small.md"
    await create_test_file(small_file, small_content)

    rel_path = small_file.relative_to(project_dir).as_posix()
    checksum = await sync_service.file_service.compute_checksum(rel_path)

    # Verify checksum is correct
    expected = hashlib.sha256(small_content.encode("utf-8")).hexdigest()
    assert checksum == expected
    assert len(checksum) == 64  # SHA256 hex digest length
