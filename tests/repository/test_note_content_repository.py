"""Tests for the NoteContentRepository."""

from datetime import datetime, timedelta, timezone

import pytest

from basic_memory import db
from basic_memory.models import NoteContent, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import (
    AcceptedNoteContentWrite,
    NoteContentRepository,
)
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
    repository = NoteContentRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        created = await repository.create(session, build_note_content_payload(sample_entity.id))

        assert created.entity_id == sample_entity.id
        assert created.project_id == sample_entity.project_id
        assert created.external_id == sample_entity.external_id
        assert created.file_path == sample_entity.file_path

        by_entity = await repository.get_by_entity_id(session, sample_entity.id)
        by_external = await repository.get_by_external_id(session, sample_entity.external_id)
        by_path = await repository.get_by_file_path(session, sample_entity.file_path)

    assert by_entity is not None
    assert by_external is not None
    assert by_path is not None
    assert by_entity.entity_id == created.entity_id
    assert by_external.entity_id == created.entity_id
    assert by_path.entity_id == created.entity_id


@pytest.mark.asyncio
async def test_accept_write_inserts_pending_snapshot(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Accepted DB-first note writes should insert pending materialization state."""
    repository = NoteContentRepository(project_id=test_project.id)
    updated_at = datetime.now(timezone.utc)

    async with db.scoped_session(session_maker) as session:
        created = await repository.accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=sample_entity.id,
                markdown_content="# Accepted content",
                db_version=1,
                db_checksum="db-checksum-1",
                last_source="api",
                updated_at=updated_at,
            ),
        )

    assert created.entity_id == sample_entity.id
    assert created.project_id == sample_entity.project_id
    assert created.external_id == sample_entity.external_id
    assert created.file_path == sample_entity.file_path
    assert created.markdown_content == "# Accepted content"
    assert created.db_version == 1
    assert created.db_checksum == "db-checksum-1"
    assert created.file_write_status == "pending"
    assert created.last_source == "api"
    assert created.file_version is None
    assert created.file_checksum is None
    assert created.file_updated_at is None
    assert created.last_materialization_error is None
    assert created.last_materialization_attempt_at is None


@pytest.mark.asyncio
async def test_accept_write_updates_snapshot_without_forgetting_file_state(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """A newer accepted DB write should preserve last materialized file metadata."""
    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.create(session, build_note_content_payload(sample_entity.id))
        current = await repository.get_by_entity_id(session, sample_entity.id)
        assert current is not None
        current.file_version = 3
        current.file_checksum = "file-checksum-3"
        current.file_write_status = "external_change_detected"
        current.last_materialization_error = "conflict"
        current.last_materialization_attempt_at = datetime.now(timezone.utc)
        await session.flush()

        updated_at = datetime.now(timezone.utc)
        updated = await repository.accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=sample_entity.id,
                markdown_content="# New accepted content",
                db_version=2,
                db_checksum="db-checksum-2",
                last_source="mcp",
                updated_at=updated_at,
            ),
        )

    assert updated.entity_id == sample_entity.id
    assert updated.markdown_content == "# New accepted content"
    assert updated.db_version == 2
    assert updated.db_checksum == "db-checksum-2"
    assert updated.file_write_status == "pending"
    assert updated.last_source == "mcp"
    assert updated.updated_at == updated_at
    assert updated.last_materialization_error is None
    assert updated.last_materialization_attempt_at is None
    assert updated.file_version == 3
    assert updated.file_checksum == "file-checksum-3"


@pytest.mark.asyncio
async def test_upsert_updates_existing_note_content(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Upsert should update the existing row instead of inserting a duplicate."""
    repository = NoteContentRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        await repository.create(session, build_note_content_payload(sample_entity.id))

        updated_at = datetime.now(timezone.utc)
        updated = await repository.upsert(
            session,
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
            ),
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
async def test_upsert_inserts_when_no_existing_row(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Upsert should insert a new row when the entity has no note_content yet."""
    repository = NoteContentRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        created = await repository.upsert(session, build_note_content_payload(sample_entity.id))

    assert created.entity_id == sample_entity.id
    assert created.project_id == sample_entity.project_id
    assert created.external_id == sample_entity.external_id
    assert created.file_path == sample_entity.file_path
    assert created.db_version == 1


@pytest.mark.asyncio
async def test_create_requires_entity_id(session_maker, test_project: Project):
    """Create should fail fast when note_content identity is missing."""
    repository = NoteContentRepository(project_id=test_project.id)

    with pytest.raises(ValueError, match="entity_id is required"):
        async with db.scoped_session(session_maker) as session:
            await repository.create(session, {"markdown_content": "# Missing entity"})


@pytest.mark.asyncio
async def test_upsert_preserves_existing_fields_for_partial_payload(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Partial upserts should only change explicit fields and preserve existing state."""
    repository = NoteContentRepository(project_id=test_project.id)
    payload = build_note_content_payload(sample_entity.id)
    payload["last_materialization_error"] = "stale failure"
    async with db.scoped_session(session_maker) as session:
        created = await repository.create(session, payload)

        updated_at = datetime.now(timezone.utc)
        updated = await repository.upsert(
            session,
            {
                "entity_id": sample_entity.id,
                "markdown_content": "# Partially updated content",
                "db_version": 2,
                "updated_at": updated_at,
                "last_materialization_error": None,
            },
        )

        assert updated.markdown_content == "# Partially updated content"
        assert updated.db_version == 2
        assert updated.db_checksum == created.db_checksum
        assert updated.file_write_status == created.file_write_status
        assert updated.last_source == created.last_source
        assert updated.last_materialization_error is None
        assert updated.file_path == sample_entity.file_path


@pytest.mark.asyncio
async def test_create_rejects_missing_entity(session_maker, test_project: Project):
    """Create should fail when the owning entity does not exist."""
    repository = NoteContentRepository(project_id=test_project.id)

    with pytest.raises(ValueError, match="Entity 999999 does not exist"):
        async with db.scoped_session(session_maker) as session:
            await repository.create(session, build_note_content_payload(999999))


@pytest.mark.asyncio
async def test_create_rejects_entity_from_another_project(session_maker, config_home):
    """Create should reject note_content writes across project boundaries."""
    project_repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        project_one = await project_repository.create(
            session,
            {
                "name": "project-one-boundary",
                "path": str(config_home / "project-one-boundary"),
                "is_active": True,
            },
        )
        project_two = await project_repository.create(
            session,
            {
                "name": "project-two-boundary",
                "path": str(config_home / "project-two-boundary"),
                "is_active": True,
            },
        )
        entity_repository = EntityRepository(project_id=project_two.id)
        other_project_entity = await entity_repository.create(
            session,
            {
                "title": "Other Project Note",
                "note_type": "test",
                "permalink": "project-two/other-project-note",
                "file_path": "notes/other-project-note.md",
                "content_type": "text/markdown",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )

        repository = NoteContentRepository(project_id=project_one.id)

        with pytest.raises(
            ValueError,
            match=f"Entity {other_project_entity.id} belongs to project {project_two.id}",
        ):
            await repository.create(session, build_note_content_payload(other_project_entity.id))


@pytest.mark.asyncio
async def test_update_state_fields_realigns_identity_with_entity(
    session_maker,
    test_project: Project,
    sample_entity,
    entity_repository: EntityRepository,
):
    """Sync-field updates should refresh mirrored identity from the owning entity."""
    repository = NoteContentRepository(project_id=test_project.id)

    renamed_path = "renamed/test_entity.md"
    async with db.scoped_session(session_maker) as session:
        await repository.create(session, build_note_content_payload(sample_entity.id))
        await entity_repository.update(session, sample_entity.id, {"file_path": renamed_path})

        updated = await repository.update_state_fields(
            session,
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
async def test_update_state_fields_rejects_invalid_fields(
    session_maker,
    test_project: Project,
    sample_entity,
):
    """Only the declared mutable sync fields should be accepted."""
    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.create(session, build_note_content_payload(sample_entity.id))

        with pytest.raises(ValueError, match="Unsupported note_content update fields: file_path"):
            await repository.update_state_fields(
                session, sample_entity.id, file_path="renamed/note.md"
            )


@pytest.mark.asyncio
async def test_update_state_fields_returns_none_for_missing_note_content(
    session_maker,
    test_project: Project,
):
    """Missing note_content rows should produce a clean None response."""
    repository = NoteContentRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        assert (
            await repository.update_state_fields(session, 999999, file_write_status="failed")
            is None
        )


@pytest.mark.asyncio
async def test_delete_by_entity_id(session_maker, test_project: Project, sample_entity):
    """Delete note_content directly by entity identifier."""
    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.create(session, build_note_content_payload(sample_entity.id))

        deleted = await repository.delete_by_entity_id(session, sample_entity.id)

        assert deleted is True
        assert await repository.get_by_entity_id(session, sample_entity.id) is None


@pytest.mark.asyncio
async def test_delete_by_entity_id_returns_false_when_missing(
    session_maker,
    test_project: Project,
):
    """Delete should report False when the note_content row does not exist."""
    repository = NoteContentRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        assert await repository.delete_by_entity_id(session, 999999) is False


@pytest.mark.asyncio
async def test_note_content_cascades_when_entity_is_deleted(
    session_maker,
    test_project: Project,
    sample_entity,
    entity_repository: EntityRepository,
):
    """Deleting the owning entity should cascade to note_content."""
    repository = NoteContentRepository(project_id=test_project.id)

    async with db.scoped_session(session_maker) as session:
        await repository.create(session, build_note_content_payload(sample_entity.id))
        deleted = await entity_repository.delete(session, sample_entity.id)

        assert deleted is True
        assert await repository.get_by_entity_id(session, sample_entity.id) is None


@pytest.mark.asyncio
async def test_note_content_file_path_lookup_is_project_scoped(session_maker, config_home):
    """Lookups by file_path should respect the repository project scope."""
    project_repository = ProjectRepository()

    shared_file_path = "shared/note.md"
    async with db.scoped_session(session_maker) as session:
        project_one = await project_repository.create(
            session,
            {
                "name": "project-one",
                "path": str(config_home / "project-one"),
                "is_active": True,
            },
        )
        project_two = await project_repository.create(
            session,
            {
                "name": "project-two",
                "path": str(config_home / "project-two"),
                "is_active": True,
            },
        )

        entity_one_repo = EntityRepository(project_id=project_one.id)
        entity_two_repo = EntityRepository(project_id=project_two.id)

        entity_one = await entity_one_repo.create(
            session,
            {
                "title": "Shared Note",
                "note_type": "test",
                "permalink": "project-one/shared-note",
                "file_path": shared_file_path,
                "content_type": "text/markdown",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )
        entity_two = await entity_two_repo.create(
            session,
            {
                "title": "Shared Note",
                "note_type": "test",
                "permalink": "project-two/shared-note",
                "file_path": shared_file_path,
                "content_type": "text/markdown",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )

        repository_one = NoteContentRepository(project_id=project_one.id)
        repository_two = NoteContentRepository(project_id=project_two.id)
        await repository_one.create(session, build_note_content_payload(entity_one.id))
        await repository_two.create(session, build_note_content_payload(entity_two.id))

        found_one = await repository_one.get_by_file_path(session, shared_file_path)
        found_two = await repository_two.get_by_file_path(session, shared_file_path)

    assert found_one is not None
    assert found_two is not None
    assert found_one.entity_id == entity_one.id
    assert found_two.entity_id == entity_two.id


@pytest.mark.asyncio
async def test_note_content_file_path_lookup_prefers_entity_with_current_path(
    session_maker,
    config_home,
):
    """File-path lookup should prefer the entity whose current path still matches."""
    project_repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        project = await project_repository.create(
            session,
            {
                "name": "project-path-drift",
                "path": str(config_home / "project-path-drift"),
                "is_active": True,
            },
        )
        entity_repository = EntityRepository(project_id=project.id)

        stale_entity = await entity_repository.create(
            session,
            {
                "title": "Stale Note",
                "note_type": "test",
                "permalink": "project/stale-note",
                "file_path": "archived/note.md",
                "content_type": "text/markdown",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )
        current_entity = await entity_repository.create(
            session,
            {
                "title": "Current Note",
                "note_type": "test",
                "permalink": "project/current-note",
                "file_path": "shared/note.md",
                "content_type": "text/markdown",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )

        repository = NoteContentRepository(project_id=project.id)
        stale_payload = build_note_content_payload(stale_entity.id)
        stale_payload["updated_at"] = datetime.now(timezone.utc) + timedelta(minutes=5)
        await repository.create(session, stale_payload)
        await repository.create(session, build_note_content_payload(current_entity.id))

        stale_note_content = await repository.select_by_id(session, stale_entity.id)
        assert stale_note_content is not None
        stale_note_content.file_path = "shared/note.md"
        await session.flush()

        found = await repository.get_by_file_path(session, "shared/note.md")

    assert found is not None
    assert found.entity_id == current_entity.id
