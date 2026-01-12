"""Tests for issue #187 - UNIQUE constraint violation on file_path during sync."""

import pytest
from datetime import datetime, timezone

from basic_memory.models.knowledge import Entity, Observation
from basic_memory.repository.entity_repository import EntityRepository


@pytest.mark.asyncio
async def test_upsert_entity_with_observations_conflict(entity_repository: EntityRepository):
    """Test upserting an entity that already exists with observations.

    This reproduces issue #187 where sync fails with UNIQUE constraint violations
    when trying to update entities that already exist with observations.
    """
    # Create initial entity with observations
    entity1 = Entity(
        project_id=entity_repository.project_id,
        title="Original Title",
        entity_type="note",
        permalink="debugging/backup-system/coderabbit-feedback-resolution",
        file_path="debugging/backup-system/CodeRabbit Feedback Resolution - Backup System Issues.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Add observations to the entity
    obs1 = Observation(
        project_id=entity_repository.project_id,
        content="This is a test observation",
        category="testing",
        tags=["test"],
    )
    entity1.observations.append(obs1)

    result1 = await entity_repository.upsert_entity(entity1)
    original_id = result1.id

    # Verify entity was created with observations
    assert result1.id is not None
    assert len(result1.observations) == 1

    # Now try to upsert the same file_path with different content/observations
    # This simulates a file being modified and re-synced
    entity2 = Entity(
        project_id=entity_repository.project_id,
        title="Updated Title",
        entity_type="note",
        permalink="debugging/backup-system/coderabbit-feedback-resolution",  # Same permalink
        file_path="debugging/backup-system/CodeRabbit Feedback Resolution - Backup System Issues.md",  # Same file_path
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Add different observations
    obs2 = Observation(
        project_id=entity_repository.project_id,
        content="This is an updated observation",
        category="updated",
        tags=["updated"],
    )
    obs3 = Observation(
        project_id=entity_repository.project_id,
        content="This is a second observation",
        category="second",
        tags=["second"],
    )
    entity2.observations.extend([obs2, obs3])

    # This should UPDATE the existing entity, not fail with IntegrityError
    result2 = await entity_repository.upsert_entity(entity2)

    # Should update existing entity (same ID)
    assert result2.id == original_id
    assert result2.title == "Updated Title"
    assert result2.file_path == entity1.file_path
    assert result2.permalink == entity1.permalink

    # Observations should be updated
    assert len(result2.observations) == 2
    assert result2.observations[0].content == "This is an updated observation"
    assert result2.observations[1].content == "This is a second observation"


@pytest.mark.asyncio
async def test_upsert_entity_repeated_sync_same_file(entity_repository: EntityRepository):
    """Test that syncing the same file multiple times doesn't cause IntegrityError.

    This tests the specific scenario from issue #187 where files are being
    synced repeatedly and hitting UNIQUE constraint violations.
    """
    file_path = "processes/Complete Process for Uploading New Training Videos.md"
    permalink = "processes/complete-process-for-uploading-new-training-videos"

    # Create initial entity
    entity1 = Entity(
        project_id=entity_repository.project_id,
        title="Complete Process for Uploading New Training Videos",
        entity_type="note",
        permalink=permalink,
        file_path=file_path,
        content_type="text/markdown",
        checksum="abc123",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result1 = await entity_repository.upsert_entity(entity1)
    first_id = result1.id

    # Simulate multiple sync attempts (like the infinite retry loop in the issue)
    for i in range(5):
        entity_new = Entity(
            project_id=entity_repository.project_id,
            title="Complete Process for Uploading New Training Videos",
            entity_type="note",
            permalink=permalink,
            file_path=file_path,
            content_type="text/markdown",
            checksum=f"def{456 + i}",  # Different checksum each time
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        # Each upsert should succeed and update the existing entity
        result = await entity_repository.upsert_entity(entity_new)

        # Should always return the same entity (updated)
        assert result.id == first_id
        assert result.checksum == entity_new.checksum
        assert result.file_path == file_path
        assert result.permalink == permalink
