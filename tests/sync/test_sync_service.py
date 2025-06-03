"""Test general sync behavior."""

import asyncio
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
    await create_test_file(project_dir / "target_doc.md", target_content)

    # Sync again - should resolve the reference
    await sync_service.sync(project_config.home)

    # Verify reference is now resolved
    source = await entity_service.get_by_permalink("source")
    target = await entity_service.get_by_permalink("target-doc")
    assert len(source.relations) == 1
    assert source.relations[0].to_id == target.id
    assert source.relations[0].to_name == target.title


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
    assert entity_b.id in a_outgoing_targets, f"A should depend on B. A's targets: {a_outgoing_targets}, B's ID: {entity_b.id}"
    assert entity_c.id in a_outgoing_targets, f"A should depend on C. A's targets: {a_outgoing_targets}, C's ID: {entity_c.id}"
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
    assert (project_config.home).exists()


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
    for filename, _ in test_files.items():
        content: str = """
---
type: knowledge
created: 2024-01-01
modified: 2024-01-01
---
# Test File

Testing permalink generation.
"""
        await create_test_file(project_config.home / filename, content)

        # Run sync
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
    project_config: ProjectConfig,
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

    rel_results = await search_service.search(SearchQuery(text="connects_to"))
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
    file_path = project_dir / "file_dates.md"
    await create_test_file(file_path, file_dates_content)

    # Run sync
    await sync_service.sync(project_config.home)

    # Check explicit frontmatter dates
    explicit_entity = await entity_service.get_by_permalink("explicit-dates")
    assert explicit_entity.created_at is not None
    assert explicit_entity.updated_at is not None

    # Check file timestamps
    file_entity = await entity_service.get_by_permalink("file-dates")
    file_stats = file_path.stat()
    assert (
        abs((file_entity.created_at.timestamp() - file_stats.st_ctime)) < 1
    )  # Allow 1s difference
    assert (
        abs((file_entity.updated_at.timestamp() - file_stats.st_mtime)) < 1
    )  # Allow 1s difference


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

    # Sync again
    await sync_service.sync(project_config.home)

    # Check search index has updated path
    results = await search_service.search(SearchQuery(text="Content for move test"))
    assert len(results) == 1
    assert results[0].file_path == str(new_path.relative_to(project_dir))


@pytest.mark.asyncio
async def test_sync_null_checksum_cleanup(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
    app_config,
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
    sync_service.project_config = project_config

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
