"""Tests for the entity repository UPSERT functionality."""

import pytest
from datetime import datetime, timezone

from basic_memory.models.knowledge import Entity
from basic_memory.repository.entity_repository import EntityRepository


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
        file_path="test/second-file.md",   # Different file_path
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
            title=f"Entity {i+1}",
            entity_type="note",
            permalink=base_permalink,  # All try to use same permalink
            file_path=f"test/file-{i+1}.md",  # Different file paths
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
    """Test that upsert handles race condition where file_path conflict occurs after initial check."""
    from unittest.mock import patch
    from sqlalchemy.exc import IntegrityError
    
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
    
    # Create another entity with different file_path and permalink
    entity2 = Entity(
        project_id=entity_repository.project_id,
        title="Race Condition Test",
        entity_type="note", 
        permalink="test/race-entity",
        file_path="test/different-file.md",  # Different initially
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    # Now simulate race condition: change file_path to conflict after the initial check
    original_add = entity_repository.session_maker().add
    call_count = 0
    
    def mock_add(obj):
        nonlocal call_count
        if isinstance(obj, Entity) and call_count == 0:
            call_count += 1
            # Simulate race condition by changing file_path to conflict
            obj.file_path = "test/race-file.md"  # Same as entity1
            # This should trigger IntegrityError for file_path constraint
            raise IntegrityError("UNIQUE constraint failed: entity.file_path", None, None)
        return original_add(obj)
    
    # Mock session.add to simulate the race condition
    with patch.object(entity_repository.session_maker().__class__, 'add', side_effect=mock_add):
        # This should handle the race condition gracefully by updating the existing entity
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
            title=f"Entity {i+1}",
            entity_type="note",
            permalink=permalink,
            file_path=f"test/gap-file-{i+1}.md",
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