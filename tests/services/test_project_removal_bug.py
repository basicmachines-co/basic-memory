"""Test for project removal bug #254."""

import os
import tempfile
from datetime import timezone, datetime
from pathlib import Path

import pytest

from basic_memory.services.project_service import ProjectService


@pytest.mark.asyncio
async def test_remove_project_with_related_entities(project_service: ProjectService):
    """Test removing a project that has related entities (reproduces issue #254).

    This test verifies that projects with related entities (entities, observations, relations)
    can be properly deleted without foreign key constraint violations.

    The bug was caused by missing foreign key constraints with CASCADE DELETE after
    the project table was recreated in migration 647e7a75e2cd.
    """
    test_project_name = f"test-remove-with-entities-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-remove-with-entities")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        try:
            # Step 1: Add the test project
            await project_service.add_project(test_project_name, test_project_path)

            # Verify project exists
            project = await project_service.get_project(test_project_name)
            assert project is not None

            # Step 2: Create related entities for this project
            from basic_memory.repository.entity_repository import EntityRepository

            entity_repo = EntityRepository(
                project_service.repository.session_maker, project_id=project.id
            )

            entity_data = {
                "title": "Test Entity for Deletion",
                "entity_type": "note",
                "content_type": "text/markdown",
                "project_id": project.id,
                "permalink": "test-deletion-entity",
                "file_path": "test-deletion-entity.md",
                "checksum": "test123",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            entity = await entity_repo.create(entity_data)
            assert entity is not None

            # Step 3: Create observations for the entity
            from basic_memory.repository.observation_repository import ObservationRepository

            obs_repo = ObservationRepository(
                project_service.repository.session_maker, project_id=project.id
            )

            observation_data = {
                "entity_id": entity.id,
                "content": "This is a test observation",
                "category": "note",
            }
            observation = await obs_repo.create(observation_data)
            assert observation is not None

            # Step 4: Create relations involving the entity
            from basic_memory.repository.relation_repository import RelationRepository

            rel_repo = RelationRepository(
                project_service.repository.session_maker, project_id=project.id
            )

            relation_data = {
                "from_id": entity.id,
                "to_name": "some-target-entity",
                "relation_type": "relates-to",
            }
            relation = await rel_repo.create(relation_data)
            assert relation is not None

            # Step 5: Attempt to remove the project
            # This should work with proper cascade delete, or fail with foreign key constraint
            await project_service.remove_project(test_project_name)

            # Step 6: Verify everything was properly deleted

            # Project should be gone
            removed_project = await project_service.get_project(test_project_name)
            assert removed_project is None, "Project should have been removed"

            # Related entities should be cascade deleted
            remaining_entity = await entity_repo.find_by_id(entity.id)
            assert remaining_entity is None, "Entity should have been cascade deleted"

            # Observations should be cascade deleted
            remaining_obs = await obs_repo.find_by_id(observation.id)
            assert remaining_obs is None, "Observation should have been cascade deleted"

            # Relations should be cascade deleted
            remaining_rel = await rel_repo.find_by_id(relation.id)
            assert remaining_rel is None, "Relation should have been cascade deleted"

        except Exception as e:
            # Check if this is the specific foreign key constraint error from the bug report
            if "FOREIGN KEY constraint failed" in str(e):
                pytest.fail(
                    f"Bug #254 reproduced: {e}. "
                    "This indicates missing foreign key constraints with CASCADE DELETE. "
                    "Run migration a1b2c3d4e5f6_fix_project_foreign_keys.py to fix this."
                )
            else:
                # Re-raise other unexpected errors
                raise e

        finally:
            # Clean up - remove project if it still exists
            if test_project_name in project_service.projects:
                try:
                    await project_service.remove_project(test_project_name)
                except Exception:
                    # Manual cleanup if remove_project fails
                    try:
                        project_service.config_manager.remove_project(test_project_name)
                    except Exception:
                        pass

                    project = await project_service.get_project(test_project_name)
                    if project:
                        await project_service.repository.delete(project.id)
