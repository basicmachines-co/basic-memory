"""Tests for the NoteContentRepository."""

from datetime import datetime, timezone

import pytest

from basic_memory.models import NoteContent, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.project_repository import ProjectRepository


def build_note_content_payload(entity_id: int) -> dict:
    """Build a minimal payload for note_content writes."""
    return {
        "entity_id": entity_id,
        "project_id": -1,
        "external_id": "stale-external-id",
        "file_path": "stale/path.md",
        "markdown_content": "# Materialized content",
        "db_version": 1,
        "db_checksum": "db-checksum-1",
        "file_version": None,
        "file_checksum": None,
        "file_write_status": "pending",
        "last_source": "api",
        "updated_at": datetime.now(timezone.utc),
        "file_updated_at": None,
        "last_materialization_error": None,
        "last_materialization_attempt_at": None,
    }


@pytest.mark.asyncio
async def test_create_and_lookup_note_content(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Create note_content and read it back through each supported lookup."""
    repository = NoteContentRepository(session_maker, project_id=test_project.id)

    created = await repository.create(build_note_content_payload(sample_entity.id))

    assert created.entity_id == sample_entity.id
    assert created.project_id == sample_entity.project_id
    assert created.external_id == sample_entity.external_id
    assert created.file_path == sample_entity.file_path

    by_entity = await repository.get_by_entity_id(sample_entity.id)
    by_external = await repository.get_by_external_id(sample_entity.external_id)
    by_path = await repository.get_by_file_path(sample_entity.file_path)

    assert by_entity is not None
    assert by_external is not None
    assert by_path is not None
    assert by_entity.entity_id == created.entity_id
    assert by_external.entity_id == created.entity_id
    assert by_path.entity_id == created.entity_id


@pytest.mark.asyncio
async def test_upsert_updates_existing_note_content(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Upsert should update the existing row instead of inserting a duplicate."""
    repository = NoteContentRepository(session_maker, project_id=test_project.id)
    await repository.create(build_note_content_payload(sample_entity.id))

    updated_at = datetime.now(timezone.utc)
    updated = await repository.upsert(
        NoteContent(
            entity_id=sample_entity.id,
            project_id=test_project.id,
            external_id=sample_entity.external_id,
            file_path=sample_entity.file_path,
            markdown_content="# Updated materialized content",
            db_version=2,
            db_checksum="db-checksum-2",
            file_version=7,
            file_checksum="file-checksum-7",
            file_write_status="synced",
            last_source="reconciler",
            updated_at=updated_at,
            file_updated_at=updated_at,
            last_materialization_error="transient failure",
            last_materialization_attempt_at=updated_at,
        )
    )

    assert updated.entity_id == sample_entity.id
    assert updated.markdown_content == "# Updated materialized content"
    assert updated.db_version == 2
    assert updated.db_checksum == "db-checksum-2"
    assert updated.file_version == 7
    assert updated.file_checksum == "file-checksum-7"
    assert updated.file_write_status == "synced"
    assert updated.last_source == "reconciler"
    assert updated.last_materialization_error == "transient failure"


@pytest.mark.asyncio
async def test_update_state_fields_realigns_identity_with_entity(
    session_maker,
    test_project: Project,
    sample_entity,
    entity_repository: EntityRepository,
):
    """Sync-field updates should refresh mirrored identity from the owning entity."""
    repository = NoteContentRepository(session_maker, project_id=test_project.id)
    await repository.create(build_note_content_payload(sample_entity.id))

    renamed_path = "renamed/test_entity.md"
    await entity_repository.update(sample_entity.id, {"file_path": renamed_path})

    updated = await repository.update_state_fields(
        sample_entity.id,
        file_write_status="failed",
        file_version=3,
        file_checksum="file-checksum-3",
        last_materialization_error=None,
        last_materialization_attempt_at=None,
    )

    assert updated is not None
    assert updated.file_path == renamed_path
    assert updated.external_id == sample_entity.external_id
    assert updated.file_write_status == "failed"
    assert updated.file_version == 3
    assert updated.file_checksum == "file-checksum-3"
    assert updated.last_materialization_error is None
    assert updated.last_materialization_attempt_at is None


@pytest.mark.asyncio
async def test_delete_by_entity_id(session_maker, test_project: Project, sample_entity):
    """Delete note_content directly by entity identifier."""
    repository = NoteContentRepository(session_maker, project_id=test_project.id)
    await repository.create(build_note_content_payload(sample_entity.id))

    deleted = await repository.delete_by_entity_id(sample_entity.id)

    assert deleted is True
    assert await repository.get_by_entity_id(sample_entity.id) is None


@pytest.mark.asyncio
async def test_note_content_cascades_when_entity_is_deleted(
    session_maker,
    test_project: Project,
    sample_entity,
    entity_repository: EntityRepository,
):
    """Deleting the owning entity should cascade to note_content."""
    repository = NoteContentRepository(session_maker, project_id=test_project.id)
    await repository.create(build_note_content_payload(sample_entity.id))

    deleted = await entity_repository.delete(sample_entity.id)

    assert deleted is True
    assert await repository.get_by_entity_id(sample_entity.id) is None


@pytest.mark.asyncio
async def test_note_content_file_path_lookup_is_project_scoped(session_maker, config_home):
    """Lookups by file_path should respect the repository project scope."""
    project_repository = ProjectRepository(session_maker)
    project_one = await project_repository.create(
        {
            "name": "project-one",
            "path": str(config_home / "project-one"),
            "is_active": True,
        }
    )
    project_two = await project_repository.create(
        {
            "name": "project-two",
            "path": str(config_home / "project-two"),
            "is_active": True,
        }
    )

    entity_one_repo = EntityRepository(session_maker, project_id=project_one.id)
    entity_two_repo = EntityRepository(session_maker, project_id=project_two.id)

    shared_file_path = "shared/note.md"
    entity_one = await entity_one_repo.create(
        {
            "title": "Shared Note",
            "note_type": "test",
            "permalink": "project-one/shared-note",
            "file_path": shared_file_path,
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )
    entity_two = await entity_two_repo.create(
        {
            "title": "Shared Note",
            "note_type": "test",
            "permalink": "project-two/shared-note",
            "file_path": shared_file_path,
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    repository_one = NoteContentRepository(session_maker, project_id=project_one.id)
    repository_two = NoteContentRepository(session_maker, project_id=project_two.id)
    await repository_one.create(build_note_content_payload(entity_one.id))
    await repository_two.create(build_note_content_payload(entity_two.id))

    found_one = await repository_one.get_by_file_path(shared_file_path)
    found_two = await repository_two.get_by_file_path(shared_file_path)

    assert found_one is not None
    assert found_two is not None
    assert found_one.entity_id == entity_one.id
    assert found_two.entity_id == entity_two.id
