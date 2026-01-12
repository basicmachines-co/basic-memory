"""Tests for the entity repository UPSERT functionality."""

import pytest
from datetime import datetime, timezone

from basic_memory.models.knowledge import Entity
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.exceptions import SyncFatalError


@pytest.mark.asyncio
async def test_upsert_entity_new_entity(entity_repository: EntityRepository):
    """Test upserting a completely new entity."""
    entity = Entity(
        project_id=entity_repository.project_id,
        title="Test Entity",
        entity_type="note",
        permalink="test/test-entity",
        file_path="test/test-entity.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = await entity_repository.upsert_entity(entity)

    assert result.id is not None
    assert result.title == "Test Entity"
    assert result.permalink == "test/test-entity"
    assert result.file_path == "test/test-entity.md"


@pytest.mark.asyncio
async def test_upsert_entity_same_file_update(entity_repository: EntityRepository):
    """Test upserting an entity that already exists with same file_path."""
    # Create initial entity
    entity1 = Entity(
        project_id=entity_repository.project_id,
        title="Original Title",
        entity_type="note",
        permalink="test/test-entity",
        file_path="test/test-entity.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result1 = await entity_repository.upsert_entity(entity1)
    original_id = result1.id

    # Update with same file_path and permalink
    entity2 = Entity(
        project_id=entity_repository.project_id,
        title="Updated Title",
        entity_type="note",
        permalink="test/test-entity",  # Same permalink
        file_path="test/test-entity.md",  # Same file_path
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result2 = await entity_repository.upsert_entity(entity2)

    # Should update existing entity (same ID)
    assert result2.id == original_id
    assert result2.title == "Updated Title"
    assert result2.permalink == "test/test-entity"
    assert result2.file_path == "test/test-entity.md"


@pytest.mark.asyncio
async def test_upsert_entity_permalink_conflict_different_file(entity_repository: EntityRepository):
    """Test upserting an entity with permalink conflict but different file_path."""
    # Create initial entity
    entity1 = Entity(
        project_id=entity_repository.project_id,
        title="First Entity",
        entity_type="note",
        permalink="test/shared-permalink",
        file_path="test/first-file.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result1 = await entity_repository.upsert_entity(entity1)
    first_id = result1.id

    # Try to create entity with same permalink but different file_path
    entity2 = Entity(
        project_id=entity_repository.project_id,
        title="Second Entity",
        entity_type="note",
        permalink="test/shared-permalink",  # Same permalink
        file_path="test/second-file.md",  # Different file_path
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result2 = await entity_repository.upsert_entity(entity2)

    # Should create new entity with unique permalink
    assert result2.id != first_id
    assert result2.title == "Second Entity"
    assert result2.permalink == "test/shared-permalink-1"  # Should get suffix
    assert result2.file_path == "test/second-file.md"

    # Original entity should be unchanged
    original = await entity_repository.get_by_permalink("test/shared-permalink")
    assert original is not None
    assert original.id == first_id
    assert original.title == "First Entity"


@pytest.mark.asyncio
async def test_upsert_entity_multiple_permalink_conflicts(entity_repository: EntityRepository):
    """Test upserting multiple entities with permalink conflicts."""
    base_permalink = "test/conflict"

    # Create entities with conflicting permalinks
    entities = []
    for i in range(3):
        entity = Entity(
            project_id=entity_repository.project_id,
            title=f"Entity {i + 1}",
            entity_type="note",
            permalink=base_permalink,  # All try to use same permalink
            file_path=f"test/file-{i + 1}.md",  # Different file paths
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        result = await entity_repository.upsert_entity(entity)
        entities.append(result)

    # Verify permalinks are unique
    expected_permalinks = ["test/conflict", "test/conflict-1", "test/conflict-2"]
    actual_permalinks = [entity.permalink for entity in entities]

    assert set(actual_permalinks) == set(expected_permalinks)

    # Verify all entities were created (different IDs)
    entity_ids = [entity.id for entity in entities]
    assert len(set(entity_ids)) == 3


@pytest.mark.asyncio
async def test_upsert_entity_race_condition_file_path(entity_repository: EntityRepository):
    """Test that upsert handles file_path conflicts using ON CONFLICT DO UPDATE.

    With SQLite's ON CONFLICT, race conditions are handled at the database level
    without requiring application-level checks. This test verifies that updating
    an existing entity by file_path works correctly.
    """
    # Create an entity first
    entity1 = Entity(
        project_id=entity_repository.project_id,
        title="Original Entity",
        entity_type="note",
        permalink="test/original",
        file_path="test/race-file.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result1 = await entity_repository.upsert_entity(entity1)
    original_id = result1.id

    # Create another entity with same file_path but different title and permalink
    # This simulates a concurrent update scenario
    entity2 = Entity(
        project_id=entity_repository.project_id,
        title="Race Condition Test",
        entity_type="note",
        permalink="test/race-entity",
        file_path="test/race-file.md",  # Same file path as entity1
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # ON CONFLICT should update the existing entity
    result2 = await entity_repository.upsert_entity(entity2)

    # Should return the updated original entity (same ID)
    assert result2.id == original_id
    assert result2.title == "Race Condition Test"  # Updated title
    assert result2.file_path == "test/race-file.md"  # Same file path
    assert result2.permalink == "test/race-entity"  # Updated permalink


@pytest.mark.asyncio
async def test_upsert_entity_gap_in_suffixes(entity_repository: EntityRepository):
    """Test that upsert finds the next available suffix even with gaps."""
    # Manually create entities with non-sequential suffixes
    base_permalink = "test/gap"

    # Create entities with permalinks: "test/gap", "test/gap-1", "test/gap-3"
    # (skipping "test/gap-2")
    permalinks = [base_permalink, f"{base_permalink}-1", f"{base_permalink}-3"]

    for i, permalink in enumerate(permalinks):
        entity = Entity(
            project_id=entity_repository.project_id,
            title=f"Entity {i + 1}",
            entity_type="note",
            permalink=permalink,
            file_path=f"test/gap-file-{i + 1}.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await entity_repository.add(entity)  # Use direct add to set specific permalinks

    # Now try to upsert a new entity that should get "test/gap-2"
    new_entity = Entity(
        project_id=entity_repository.project_id,
        title="Gap Filler",
        entity_type="note",
        permalink=base_permalink,  # Will conflict
        file_path="test/gap-new-file.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = await entity_repository.upsert_entity(new_entity)

    # Should get the next available suffix - our implementation finds gaps
    # so it should be "test/gap-2" (filling the gap)
    assert result.permalink == "test/gap-2"
    assert result.title == "Gap Filler"


@pytest.mark.asyncio
async def test_upsert_entity_project_scoping_isolation(session_maker):
    """Test that upsert_entity properly scopes entities by project_id.

    This test ensures that the fix for issue #167 works correctly by verifying:
    1. Entities with same permalinks/file_paths can exist in different projects
    2. Upsert operations properly scope queries by project_id
    3. No "multiple rows" errors occur when similar entities exist across projects
    """
    # Create two separate projects
    project_repository = ProjectRepository(session_maker)

    project1_data = {
        "name": "project-1",
        "description": "First test project",
        "path": "/tmp/project1",
        "is_active": True,
        "is_default": False,
    }
    project1 = await project_repository.create(project1_data)

    project2_data = {
        "name": "project-2",
        "description": "Second test project",
        "path": "/tmp/project2",
        "is_active": True,
        "is_default": False,
    }
    project2 = await project_repository.create(project2_data)

    # Create entity repositories for each project
    repo1 = EntityRepository(session_maker, project_id=project1.id)
    repo2 = EntityRepository(session_maker, project_id=project2.id)

    # Create entities with identical permalinks and file_paths in different projects
    entity1 = Entity(
        project_id=project1.id,
        title="Shared Entity",
        entity_type="note",
        permalink="docs/shared-name",
        file_path="docs/shared-name.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    entity2 = Entity(
        project_id=project2.id,
        title="Shared Entity",
        entity_type="note",
        permalink="docs/shared-name",  # Same permalink
        file_path="docs/shared-name.md",  # Same file_path
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # These should succeed without "multiple rows" errors
    result1 = await repo1.upsert_entity(entity1)
    result2 = await repo2.upsert_entity(entity2)

    # Verify both entities were created successfully
    assert result1.id is not None
    assert result2.id is not None
    assert result1.id != result2.id  # Different entities
    assert result1.project_id == project1.id
    assert result2.project_id == project2.id
    assert result1.permalink == "docs/shared-name"
    assert result2.permalink == "docs/shared-name"

    # Test updating entities in different projects (should also work without conflicts)
    entity1_update = Entity(
        project_id=project1.id,
        title="Updated Shared Entity",
        entity_type="note",
        permalink="docs/shared-name",
        file_path="docs/shared-name.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    entity2_update = Entity(
        project_id=project2.id,
        title="Also Updated Shared Entity",
        entity_type="note",
        permalink="docs/shared-name",
        file_path="docs/shared-name.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Updates should work without conflicts
    updated1 = await repo1.upsert_entity(entity1_update)
    updated2 = await repo2.upsert_entity(entity2_update)

    # Should update existing entities (same IDs)
    assert updated1.id == result1.id
    assert updated2.id == result2.id
    assert updated1.title == "Updated Shared Entity"
    assert updated2.title == "Also Updated Shared Entity"

    # Verify cross-project queries don't interfere
    found_in_project1 = await repo1.get_by_permalink("docs/shared-name")
    found_in_project2 = await repo2.get_by_permalink("docs/shared-name")

    assert found_in_project1 is not None
    assert found_in_project2 is not None
    assert found_in_project1.id == updated1.id
    assert found_in_project2.id == updated2.id
    assert found_in_project1.title == "Updated Shared Entity"
    assert found_in_project2.title == "Also Updated Shared Entity"


@pytest.mark.asyncio
async def test_upsert_entity_permalink_conflict_within_project_only(session_maker):
    """Test that permalink conflicts only occur within the same project.

    This ensures that the project scoping fix allows entities with identical
    permalinks to exist across different projects without triggering
    permalink conflict resolution.
    """
    # Create two separate projects
    project_repository = ProjectRepository(session_maker)

    project1_data = {
        "name": "conflict-project-1",
        "description": "First conflict test project",
        "path": "/tmp/conflict1",
        "is_active": True,
        "is_default": False,
    }
    project1 = await project_repository.create(project1_data)

    project2_data = {
        "name": "conflict-project-2",
        "description": "Second conflict test project",
        "path": "/tmp/conflict2",
        "is_active": True,
        "is_default": False,
    }
    project2 = await project_repository.create(project2_data)

    # Create entity repositories for each project
    repo1 = EntityRepository(session_maker, project_id=project1.id)
    repo2 = EntityRepository(session_maker, project_id=project2.id)

    # Create first entity in project1
    entity1 = Entity(
        project_id=project1.id,
        title="Original Entity",
        entity_type="note",
        permalink="test/conflict-permalink",
        file_path="test/original.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result1 = await repo1.upsert_entity(entity1)
    assert result1.permalink == "test/conflict-permalink"

    # Create entity with same permalink in project2 (should NOT get suffix)
    entity2 = Entity(
        project_id=project2.id,
        title="Cross-Project Entity",
        entity_type="note",
        permalink="test/conflict-permalink",  # Same permalink, different project
        file_path="test/cross-project.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result2 = await repo2.upsert_entity(entity2)
    # Should keep original permalink (no suffix) since it's in a different project
    assert result2.permalink == "test/conflict-permalink"

    # Now create entity with same permalink in project1 (should get suffix)
    entity3 = Entity(
        project_id=project1.id,
        title="Conflict Entity",
        entity_type="note",
        permalink="test/conflict-permalink",  # Same permalink, same project
        file_path="test/conflict.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result3 = await repo1.upsert_entity(entity3)
    # Should get suffix since it conflicts within the same project
    assert result3.permalink == "test/conflict-permalink-1"

    # Verify all entities exist correctly
    assert result1.id != result2.id != result3.id
    assert result1.project_id == project1.id
    assert result2.project_id == project2.id
    assert result3.project_id == project1.id


@pytest.mark.asyncio
async def test_upsert_entity_with_invalid_project_id(entity_repository: EntityRepository):
    """Test that upserting with non-existent project_id raises clear error.

    This tests the fix for issue #188 where sync fails with FOREIGN KEY constraint
    violations when a project is deleted during sync operations.
    """
    # Create entity with non-existent project_id
    entity = Entity(
        title="Test Entity",
        entity_type="note",
        file_path="test.md",
        permalink="test",
        project_id=99999,  # This project doesn't exist
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Should raise SyncFatalError with clear message about missing project
    with pytest.raises(SyncFatalError) as exc_info:
        await entity_repository.upsert_entity(entity)

    error_msg = str(exc_info.value)
    assert "project_id=99999 does not exist" in error_msg
    assert "project may have been deleted" in error_msg.lower()
    assert "sync will be terminated" in error_msg.lower()
