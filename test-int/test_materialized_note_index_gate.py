"""A materialized note is already indexed: its storage notification must not re-read it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from basic_memory import db
from basic_memory.indexing.file_index_checking import indexed_checksums_by_path
from basic_memory.indexing.note_materialization_runner import (
    RepositoryNoteMaterializationPublisher,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.runtime.note_content import (
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationStatus,
)
from basic_memory.runtime.note_materialization import (
    RuntimeWrittenFileState,
    plan_prepared_note_write,
)


@pytest.mark.asyncio
async def test_published_materialization_is_recognized_by_the_index_gate(
    engine_factory,
    test_project: Project,
) -> None:
    """The index gate compares the stored object's checksum with entity.checksum.

    Cloud storage reports an S3 ETag, which never equals the markdown's sha256, so the
    publisher must record the written object's storage checksum, not the content one.
    """
    _, session_maker = engine_factory
    file_path = "notes/materialized.md"
    markdown = "# Materialized\n"
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="Materialized",
            note_type="note",
            content_type="text/markdown",
            file_path=file_path,
            permalink="notes/materialized",
            checksum="etag-of-previous-object",
        )
        session.add(entity)
        await session.flush()
        entity_id = entity.id
        await NoteContentRepository(project_id=test_project.id).create(
            session,
            NoteContent(
                entity_id=entity_id,
                markdown_content=markdown,
                db_version=2,
                db_checksum="content-sha256-v2",
                file_version=1,
                file_checksum="content-sha256-v1",
                file_write_status="writing",
            ),
        )

    request = RuntimeNoteMaterializationJobRequest(
        project_id=test_project.id,
        entity_id=entity_id,
        db_version=2,
        db_checksum="content-sha256-v2",
        source="web_v2",
    )
    prepared_write = plan_prepared_note_write(
        request=request,
        file_path=file_path,
        markdown_content=markdown,
        previous_file_checksum="content-sha256-v1",
        previous_sync_checksum=None,
        attempted_at=datetime(2026, 10, 7, 3, 0, tzinfo=UTC),
    )
    written_file = RuntimeWrittenFileState(
        file_path=file_path,
        file_checksum="content-sha256-v2",
        file_updated_at=datetime(2026, 10, 7, 3, 0, 1, tzinfo=UTC),
        storage_checksum="etag-of-written-object",
    )

    result = await RepositoryNoteMaterializationPublisher(
        session_maker=session_maker
    ).publish_written_file_state(request, prepared_write, written_file)

    assert result.status is RuntimeNoteMaterializationStatus.written
    async with db.scoped_session(session_maker) as session:
        stored_entity = await session.scalar(select(Entity).where(Entity.id == entity_id))
        rows = await EntityRepository(project_id=test_project.id).get_by_file_paths(
            session, [file_path]
        )
    assert stored_entity is not None
    assert stored_entity.checksum == "etag-of-written-object"
    assert stored_entity.sync_checksum is None

    # The storage notification for this write carries the written object's ETag.
    indexed = indexed_checksums_by_path(rows)[file_path]
    assert indexed.recognizes("etag-of-written-object")
    assert not indexed.recognizes("content-sha256-v2")
