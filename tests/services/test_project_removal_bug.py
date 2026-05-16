"""Test for project removal bug #254."""

import os
import tempfile
from datetime import timezone, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from basic_memory import db
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
                "note_type": "note",
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


async def _table_exists(session_maker, table: str) -> bool:
    """Return True if the named table is present on the current connection."""
    from sqlalchemy import inspect as sa_inspect

    async with db.scoped_session(session_maker) as session:
        return await session.run_sync(
            lambda sync_session: table in sa_inspect(sync_session.connection()).get_table_names()
        )


@pytest.mark.asyncio
async def test_remove_project_purges_search_rows(project_service: ProjectService):
    """Project deletion must sweep the derived search tables.

    SQLite stores search_index as an FTS5 virtual table, which cannot carry a
    foreign key, so without an explicit purge the FTS rows survive the project
    and leak into the next project that reuses the same auto-increment id.
    Postgres has the cascade FK, but we expect the same end-state on either
    backend. This test fails on the pre-fix code: search_index still holds the
    project's rows after remove_project completes.
    """
    test_project_name = f"test-search-cleanup-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = str(Path(temp_dir) / "test-search-cleanup")
        os.makedirs(test_project_path, exist_ok=True)

        await project_service.add_project(test_project_name, test_project_path)
        project = await project_service.get_project(test_project_name)
        assert project is not None
        project_id = project.id

        # Seed both derived tables directly. The bug is in the cleanup path,
        # not the indexer, so a synthetic row is enough to prove the sweep.
        async with db.scoped_session(project_service.repository.session_maker) as session:
            await session.execute(
                text(
                    "INSERT INTO search_index "
                    "(id, title, content_stems, content_snippet, permalink, "
                    " file_path, type, project_id) "
                    "VALUES (:id, :title, :stems, :snippet, :permalink, "
                    " :file_path, :type, :project_id)"
                ),
                {
                    "id": 999_001,
                    "title": "leak canary",
                    "stems": "leak canary",
                    "snippet": "leak canary",
                    "permalink": f"leak-canary-{project_id}",
                    "file_path": "leak-canary.md",
                    "type": "entity",
                    "project_id": project_id,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO search_vector_chunks "
                    "(entity_id, project_id, chunk_key, chunk_text, source_hash, "
                    " entity_fingerprint, embedding_model) "
                    "VALUES (:entity_id, :project_id, :chunk_key, :chunk_text, "
                    " :source_hash, :entity_fingerprint, :embedding_model)"
                ),
                {
                    "entity_id": 999_001,
                    "project_id": project_id,
                    "chunk_key": "canary",
                    "chunk_text": "leak canary",
                    "source_hash": "abc",
                    "entity_fingerprint": "",
                    "embedding_model": "",
                },
            )

        async with db.scoped_session(project_service.repository.session_maker) as session:
            pre_index = (
                await session.execute(
                    text("SELECT COUNT(*) FROM search_index WHERE project_id = :pid"),
                    {"pid": project_id},
                )
            ).scalar_one()
            pre_chunks = (
                await session.execute(
                    text("SELECT COUNT(*) FROM search_vector_chunks WHERE project_id = :pid"),
                    {"pid": project_id},
                )
            ).scalar_one()
        assert pre_index >= 1, "seed row should exist before removal"
        assert pre_chunks >= 1, "seed chunk should exist before removal"

        await project_service.remove_project(test_project_name)

        async with db.scoped_session(project_service.repository.session_maker) as session:
            post_index = (
                await session.execute(
                    text("SELECT COUNT(*) FROM search_index WHERE project_id = :pid"),
                    {"pid": project_id},
                )
            ).scalar_one()
            post_chunks = (
                await session.execute(
                    text("SELECT COUNT(*) FROM search_vector_chunks WHERE project_id = :pid"),
                    {"pid": project_id},
                )
            ).scalar_one()

        assert post_index == 0, (
            f"search_index still has {post_index} rows for deleted project_id={project_id} "
            "— project deletion did not sweep the FTS table."
        )
        assert post_chunks == 0, (
            f"search_vector_chunks still has {post_chunks} rows for deleted "
            f"project_id={project_id}."
        )


@pytest.mark.asyncio
async def test_delete_returns_false_for_missing_project_id(project_service: ProjectService):
    """ProjectRepository.delete must return False when the project id is gone.

    The override loses the base Repository.delete contract if the NoResultFound
    branch isn't covered — a silent True would mislead callers into thinking
    a non-existent project was removed.
    """
    result = await project_service.repository.delete(9_999_999)
    assert result is False


@pytest.mark.asyncio
async def test_remove_project_purges_vector_embeddings(project_service: ProjectService):
    """Project deletion must also drop sqlite-vec embeddings keyed by chunk rowid.

    sqlite-vec stores vectors in a vec0 virtual table that has no cascade
    behavior. If embeddings linger after the chunks they reference are gone,
    `_run_vector_query` pulls them as top-k candidates and crowds out live
    results. The test only runs when the embeddings table is present, which
    matches the install path that exercises semantic search.
    """
    test_project_name = f"test-vec-cleanup-{os.urandom(4).hex()}"
    session_maker = project_service.repository.session_maker

    # The embeddings table only exists once semantic search has initialized.
    # Skipping when it's absent keeps this test honest on minimal CI DBs.
    if not await _table_exists(session_maker, "search_vector_embeddings"):
        pytest.skip("search_vector_embeddings is not present on this connection")

    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = str(Path(temp_dir) / "test-vec-cleanup")
        os.makedirs(test_project_path, exist_ok=True)

        await project_service.add_project(test_project_name, test_project_path)
        project = await project_service.get_project(test_project_name)
        assert project is not None
        project_id = project.id

        async with db.scoped_session(session_maker) as session:
            await session.execute(
                text(
                    "INSERT INTO search_vector_chunks "
                    "(id, entity_id, project_id, chunk_key, chunk_text, source_hash, "
                    " entity_fingerprint, embedding_model) "
                    "VALUES (:id, :entity_id, :project_id, :chunk_key, :chunk_text, "
                    " :source_hash, :entity_fingerprint, :embedding_model)"
                ),
                {
                    "id": 999_201,
                    "entity_id": 999_201,
                    "project_id": project_id,
                    "chunk_key": "vec-canary",
                    "chunk_text": "vec canary",
                    "source_hash": "abc",
                    "entity_fingerprint": "",
                    "embedding_model": "",
                },
            )
            # vec0 requires a vector matching the configured dimensions, but the
            # delete path filters by rowid; a non-existing dimension would block
            # this seed step. Skip the insert if the embeddings DDL hasn't run.
            try:
                await session.execute(
                    text(
                        "INSERT INTO search_vector_embeddings (rowid, embedding) "
                        "VALUES (:rowid, :embedding)"
                    ),
                    {"rowid": 999_201, "embedding": "[" + ",".join(["0.0"] * 384) + "]"},
                )
            except Exception:
                pytest.skip("search_vector_embeddings rejected the synthetic seed row")

        async with db.scoped_session(session_maker) as session:
            pre = (
                await session.execute(
                    text("SELECT COUNT(*) FROM search_vector_embeddings WHERE rowid = :rowid"),
                    {"rowid": 999_201},
                )
            ).scalar_one()
        assert pre >= 1, "seed embedding should exist before removal"

        await project_service.remove_project(test_project_name)

        async with db.scoped_session(session_maker) as session:
            post = (
                await session.execute(
                    text("SELECT COUNT(*) FROM search_vector_embeddings WHERE rowid = :rowid"),
                    {"rowid": 999_201},
                )
            ).scalar_one()

        assert post == 0, (
            f"search_vector_embeddings still has {post} rows for rowid 999_201 "
            "— project deletion did not sweep the sqlite-vec embeddings table."
        )
