"""Test to verify that issue #254 is fixed.

Issue #254: Foreign key constraint failures when deleting projects with related entities.

The issue was that when migration 647e7a75e2cd recreated the project table,
it did not re-establish the foreign key constraint from entity.project_id to project.id
with CASCADE DELETE, causing foreign key constraint failures when trying to delete
projects that have related entities.

Migration a1b2c3d4e5f6 was created to fix this by adding the missing foreign key
constraint with CASCADE DELETE behavior.

This test file verifies that the fix works correctly in production databases
that have had the migration applied.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from basic_memory.services.project_service import ProjectService


# @pytest.mark.skip(reason="Issue #254 not fully resolved yet - foreign key constraint errors still occur")
@pytest.mark.asyncio
async def test_issue_254_foreign_key_constraint_fix(project_service: ProjectService):
    """Test to verify issue #254 is fixed: project removal with foreign key constraints.

    This test reproduces the exact scenario from issue #254:
    1. Create a project
    2. Create entities, observations, and relations linked to that project
    3. Attempt to remove the project
    4. Verify it succeeds without "FOREIGN KEY constraint failed" errors
    5. Verify all related data is properly cleaned up via CASCADE DELETE

    Once issue #254 is fully fixed, remove the @pytest.mark.skip decorator.
    """
    test_project_name = "issue-254-verification"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "issue-254-verification")

        # Step 1: Create test project
        await project_service.add_project(test_project_name, test_project_path)
        project = await project_service.get_project(test_project_name)
        assert project is not None, "Project should be created successfully"

        # Step 2: Create related entities that would cause foreign key constraint issues
        from basic_memory.repository.entity_repository import EntityRepository
        from basic_memory.repository.observation_repository import ObservationRepository
        from basic_memory.repository.relation_repository import RelationRepository

        entity_repo = EntityRepository(
            project_service.repository.session_maker, project_id=project.id
        )
        obs_repo = ObservationRepository(
            project_service.repository.session_maker, project_id=project.id
        )
        rel_repo = RelationRepository(
            project_service.repository.session_maker, project_id=project.id
        )

        # Create entity
        entity_data = {
            "title": "Issue 254 Test Entity",
            "entity_type": "note",
            "content_type": "text/markdown",
            "project_id": project.id,
            "permalink": "issue-254-entity",
            "file_path": "issue-254-entity.md",
            "checksum": "issue254test",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        entity = await entity_repo.create(entity_data)

        # Create observation linked to entity
        observation_data = {
            "entity_id": entity.id,
            "content": "This observation should be cascade deleted",
            "category": "test",
        }
        observation = await obs_repo.create(observation_data)

        # Create relation involving the entity
        relation_data = {
            "from_id": entity.id,
            "to_name": "some-other-entity",
            "relation_type": "relates-to",
        }
        relation = await rel_repo.create(relation_data)

        # Step 3: Attempt to remove the project
        # This is where issue #254 manifested - should NOT raise "FOREIGN KEY constraint failed"
        try:
            await project_service.remove_project(test_project_name)
        except Exception as e:
            if "FOREIGN KEY constraint failed" in str(e):
                pytest.fail(
                    f"Issue #254 not fixed - foreign key constraint error still occurs: {e}. "
                    f"The migration a1b2c3d4e5f6 may not have been applied correctly or "
                    f"the CASCADE DELETE constraint is not working as expected."
                )
            else:
                # Re-raise unexpected errors
                raise

        # Step 4: Verify project was successfully removed
        removed_project = await project_service.get_project(test_project_name)
        assert removed_project is None, "Project should have been removed"

        # Step 5: Verify related data was cascade deleted
        remaining_entity = await entity_repo.find_by_id(entity.id)
        assert remaining_entity is None, "Entity should have been cascade deleted"

        remaining_observation = await obs_repo.find_by_id(observation.id)
        assert remaining_observation is None, "Observation should have been cascade deleted"

        remaining_relation = await rel_repo.find_by_id(relation.id)
        assert remaining_relation is None, "Relation should have been cascade deleted"


@pytest.mark.asyncio
async def test_issue_254_reproduction(project_service: ProjectService):
    """Test that reproduces issue #254 to document the current state.

    This test demonstrates the current behavior and will fail until the issue is fixed.
    It serves as documentation of what the problem was.
    """
    test_project_name = "issue-254-reproduction"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "issue-254-reproduction")

        # Create project and entity
        await project_service.add_project(test_project_name, test_project_path)
        project = await project_service.get_project(test_project_name)

        from basic_memory.repository.entity_repository import EntityRepository

        entity_repo = EntityRepository(
            project_service.repository.session_maker, project_id=project.id
        )

        entity_data = {
            "title": "Reproduction Entity",
            "entity_type": "note",
            "content_type": "text/markdown",
            "project_id": project.id,
            "permalink": "reproduction-entity",
            "file_path": "reproduction-entity.md",
            "checksum": "repro123",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        await entity_repo.create(entity_data)

        # This should eventually work without errors once issue #254 is fixed
        # with pytest.raises(Exception) as exc_info:
        await project_service.remove_project(test_project_name)

        # Document the current error for tracking
        # error_message = str(exc_info.value)
        # assert any(keyword in error_message for keyword in [
        #     "FOREIGN KEY constraint failed",
        #     "constraint",
        #     "integrity"
        # ]), f"Expected foreign key or integrity constraint error, got: {error_message}"
