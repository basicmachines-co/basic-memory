"""Regression tests for the note_content promotion rollback guard.

A promotion turns observed file bytes into a new accepted note_content version.
When object storage serves a stale generation of a note, those bytes equal an
accepted version that a newer accepted change already replaced. Promoting them
would silently roll the note back, so the reconciler defers unless an
authoritative storage re-read confirms the bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db, file_utils
from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconciler,
    local_promotion_confirmation_reader,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.note_file_guards import RuntimeFileChecksumReader
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)
from basic_memory.indexing.note_content_reconciliation import NoteContentReconciliationResult
from basic_memory.services.file_service import FileService

if TYPE_CHECKING:
    from loguru import Record

ACCEPTED_AT = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

FIRST_ACCEPTED = "# Note\n\nFirst accepted version.\n"
SECOND_ACCEPTED = "# Note\n\nSecond accepted version.\n"
PROMOTED_EDIT = "# Note\n\nEdited on disk after the second version.\n"
FRESH_EDIT = "# Note\n\nA brand new external edit.\n"


# --- Fixture helpers ---


@dataclass(frozen=True, slots=True)
class StaticChecksumReader:
    """Authoritative storage stand-in that reports one checksum for every path."""

    checksum: str | None

    async def exists(self, path: str) -> bool:
        return self.checksum is not None

    async def compute_checksum(self, path: str) -> str:
        assert self.checksum is not None
        return self.checksum


async def checksum(content: str) -> str:
    return await file_utils.compute_checksum(content)


async def record_accepted_versions(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    entity: Entity,
    accepted_contents: list[str],
) -> None:
    """Journal one accepted change per content, as real user writes do."""
    repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        for db_version, content in enumerate(accepted_contents, start=1):
            position = await repository.advance_partition_position(session, project.id)
            await repository.record_accepted_note_change(
                session,
                RuntimeAcceptedProjectNoteChange(
                    project_id=project.id,
                    project_external_id=project.external_id,
                    partition_position=position,
                    entity_id=entity.id,
                    note_external_id=entity.external_id,
                    permalink=entity.permalink or "test/test-entity",
                    title=entity.title,
                    operation=RuntimeProjectNoteOperation.updated,
                    file_path=entity.file_path,
                    accepted_at=ACCEPTED_AT,
                    source="api",
                    db_version=db_version,
                    db_checksum=await checksum(content),
                ),
            )


async def store_synced_note_content(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    entity: Entity,
    *,
    db_version: int,
    content: str,
) -> None:
    """Store a fully synced note_content row, the only state that can promote."""
    content_checksum = await checksum(content)
    async with db.scoped_session(session_maker) as session:
        await NoteContentRepository(project_id=project.id).create(
            session,
            NoteContent(
                entity_id=entity.id,
                markdown_content=content,
                db_version=db_version,
                db_checksum=content_checksum,
                file_version=db_version,
                file_checksum=content_checksum,
                file_write_status="synced",
                last_source="api",
                updated_at=ACCEPTED_AT,
                file_updated_at=ACCEPTED_AT,
            ),
        )


async def load_note_content(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    entity: Entity,
) -> NoteContent:
    async with db.scoped_session(session_maker) as session:
        note_content = await NoteContentRepository(project_id=project.id).get_by_entity_id(
            session, entity.id
        )
    assert note_content is not None
    return note_content


async def reconcile_observed(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    entity: Entity,
    observed_content: str,
    *,
    confirmation_reader: RuntimeFileChecksumReader | None = None,
) -> tuple[NoteContentReconciliationResult, list[Record]]:
    """Reconcile one observation and capture the promotion telemetry it logs."""
    records: list[Record] = []
    sink_id = logger.add(
        lambda message: records.append(message.record),
        level="INFO",
        filter=lambda record: str(record["extra"].get("event", "")).startswith(
            "note_content.promotion"
        ),
    )
    try:
        result = await NoteContentReconciler(
            note_content_repository=NoteContentRepository(project_id=project.id),
            session_maker=session_maker,
            promotion_confirmation_reader=confirmation_reader,
        ).reconcile(
            entity=entity,
            markdown_content=observed_content,
            observed_at=OBSERVED_AT,
            source="index",
        )
    finally:
        logger.remove(sink_id)
    return result, records


# --- Promotions that must keep working ---


@pytest.mark.asyncio
async def test_fresh_external_edit_still_promotes_and_logs(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    await record_accepted_versions(
        session_maker, test_project, sample_entity, [FIRST_ACCEPTED, SECOND_ACCEPTED]
    )
    await store_synced_note_content(
        session_maker, test_project, sample_entity, db_version=2, content=SECOND_ACCEPTED
    )

    result, records = await reconcile_observed(
        session_maker, test_project, sample_entity, FRESH_EDIT
    )

    assert result.status == "current"
    assert result.generation == 3
    stored = await load_note_content(session_maker, test_project, sample_entity)
    assert stored.db_version == 3
    assert stored.markdown_content == FRESH_EDIT

    [record] = records
    assert record["level"].name == "INFO"
    extra = record["extra"]
    assert extra["event"] == "note_content.promotion_applied"
    assert extra["entity_id"] == sample_entity.id
    assert extra["previous_db_version"] == 2
    assert extra["new_db_version"] == 3
    assert extra["observed_checksum"] == await checksum(FRESH_EDIT)
    assert extra["matched_accepted_db_version"] is None
    assert extra["newest_accepted_db_version"] == 2
    assert extra["observed_at"] == OBSERVED_AT.isoformat()
    assert extra["source"] == "index"
    assert extra["rollback_guard"] == "not_superseded"


@pytest.mark.asyncio
async def test_newest_accepted_checksum_still_promotes(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    """Restoring the newest accepted bytes over a promoted version is a legitimate repair."""
    await record_accepted_versions(
        session_maker, test_project, sample_entity, [FIRST_ACCEPTED, SECOND_ACCEPTED]
    )
    await store_synced_note_content(
        session_maker, test_project, sample_entity, db_version=3, content=PROMOTED_EDIT
    )

    result, records = await reconcile_observed(
        session_maker, test_project, sample_entity, SECOND_ACCEPTED
    )

    assert result.status == "current"
    assert result.generation == 4
    stored = await load_note_content(session_maker, test_project, sample_entity)
    assert stored.markdown_content == SECOND_ACCEPTED

    [record] = records
    assert record["extra"]["matched_accepted_db_version"] == 2
    assert record["extra"]["newest_accepted_db_version"] == 2
    assert record["extra"]["rollback_guard"] == "not_superseded"


@pytest.mark.asyncio
async def test_note_without_accepted_history_promotes_as_before(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    """Notes only ever edited on disk have no journal rows, so nothing changes for them."""
    await store_synced_note_content(
        session_maker, test_project, sample_entity, db_version=1, content=FIRST_ACCEPTED
    )

    result, records = await reconcile_observed(
        session_maker, test_project, sample_entity, SECOND_ACCEPTED
    )

    assert result.status == "current"
    assert result.generation == 2
    [record] = records
    assert record["extra"]["matched_accepted_db_version"] is None
    assert record["extra"]["newest_accepted_db_version"] is None
    assert record["extra"]["rollback_guard"] == "not_superseded"


# --- Promotions of superseded bytes ---


@pytest.mark.asyncio
async def test_superseded_checksum_defers_without_authoritative_reader(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    """A stale storage read cannot roll a note back to a replaced accepted version."""
    await record_accepted_versions(
        session_maker, test_project, sample_entity, [FIRST_ACCEPTED, SECOND_ACCEPTED]
    )
    await store_synced_note_content(
        session_maker, test_project, sample_entity, db_version=2, content=SECOND_ACCEPTED
    )

    result, records = await reconcile_observed(
        session_maker, test_project, sample_entity, FIRST_ACCEPTED
    )

    assert result.status == "deferred"
    stored = await load_note_content(session_maker, test_project, sample_entity)
    assert stored.db_version == 2
    assert stored.markdown_content == SECOND_ACCEPTED

    [record] = records
    assert record["level"].name == "WARNING"
    extra = record["extra"]
    assert extra["event"] == "note_content.promotion_refused"
    assert extra["entity_id"] == sample_entity.id
    assert extra["previous_db_version"] == 2
    assert extra["new_db_version"] is None
    assert extra["observed_checksum"] == await checksum(FIRST_ACCEPTED)
    assert extra["matched_accepted_db_version"] == 1
    assert extra["newest_accepted_db_version"] == 2
    assert extra["observed_at"] == OBSERVED_AT.isoformat()
    assert extra["source"] == "index"
    assert extra["rollback_guard"] == "no_authoritative_reader"


@pytest.mark.asyncio
async def test_superseded_checksum_defers_when_authoritative_read_disagrees(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    await record_accepted_versions(
        session_maker, test_project, sample_entity, [FIRST_ACCEPTED, SECOND_ACCEPTED]
    )
    await store_synced_note_content(
        session_maker, test_project, sample_entity, db_version=2, content=SECOND_ACCEPTED
    )

    result, records = await reconcile_observed(
        session_maker,
        test_project,
        sample_entity,
        FIRST_ACCEPTED,
        confirmation_reader=StaticChecksumReader(await checksum(SECOND_ACCEPTED)),
    )

    assert result.status == "deferred"
    stored = await load_note_content(session_maker, test_project, sample_entity)
    assert stored.markdown_content == SECOND_ACCEPTED
    [record] = records
    assert record["level"].name == "WARNING"
    assert record["extra"]["rollback_guard"] == "storage_disagrees"


@pytest.mark.asyncio
async def test_superseded_checksum_defers_when_authoritative_read_finds_no_file(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    await record_accepted_versions(
        session_maker, test_project, sample_entity, [FIRST_ACCEPTED, SECOND_ACCEPTED]
    )
    await store_synced_note_content(
        session_maker, test_project, sample_entity, db_version=2, content=SECOND_ACCEPTED
    )

    result, records = await reconcile_observed(
        session_maker,
        test_project,
        sample_entity,
        FIRST_ACCEPTED,
        confirmation_reader=StaticChecksumReader(None),
    )

    assert result.status == "deferred"
    [record] = records
    assert record["extra"]["rollback_guard"] == "storage_disagrees"


@pytest.mark.asyncio
async def test_superseded_checksum_promotes_when_authoritative_read_confirms(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    """A deliberate restore of older bytes still promotes once storage confirms it."""
    await record_accepted_versions(
        session_maker, test_project, sample_entity, [FIRST_ACCEPTED, SECOND_ACCEPTED]
    )
    await store_synced_note_content(
        session_maker, test_project, sample_entity, db_version=2, content=SECOND_ACCEPTED
    )

    result, records = await reconcile_observed(
        session_maker,
        test_project,
        sample_entity,
        FIRST_ACCEPTED,
        confirmation_reader=StaticChecksumReader(await checksum(FIRST_ACCEPTED)),
    )

    assert result.status == "current"
    assert result.generation == 3
    stored = await load_note_content(session_maker, test_project, sample_entity)
    assert stored.markdown_content == FIRST_ACCEPTED
    [record] = records
    assert record["level"].name == "INFO"
    assert record["extra"]["new_db_version"] == 3
    assert record["extra"]["matched_accepted_db_version"] == 1
    assert record["extra"]["rollback_guard"] == "confirmed_by_storage"


# --- Local filesystem keeps today's behaviour ---


@pytest.mark.asyncio
async def test_local_filesystem_confirms_restored_older_version(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
    file_service: FileService,
) -> None:
    """Local reads are authoritative, so a local revert to older bytes still promotes."""
    await record_accepted_versions(
        session_maker, test_project, sample_entity, [FIRST_ACCEPTED, SECOND_ACCEPTED]
    )
    await store_synced_note_content(
        session_maker, test_project, sample_entity, db_version=2, content=SECOND_ACCEPTED
    )
    note_path = Path(file_service.base_path) / sample_entity.file_path
    note_path.parent.mkdir(parents=True, exist_ok=True)
    # Confirmation hashes file bytes, so Windows must not translate LF to CRLF here.
    note_path.write_bytes(FIRST_ACCEPTED.encode("utf-8"))

    reader = local_promotion_confirmation_reader(file_service)
    assert reader is file_service

    result, records = await reconcile_observed(
        session_maker,
        test_project,
        sample_entity,
        FIRST_ACCEPTED,
        confirmation_reader=reader,
    )

    assert result.status == "current"
    stored = await load_note_content(session_maker, test_project, sample_entity)
    assert stored.markdown_content == FIRST_ACCEPTED
    [record] = records
    assert record["extra"]["rollback_guard"] == "confirmed_by_storage"


def test_object_storage_file_service_cannot_confirm(tmp_path: Path) -> None:
    """Object-storage file services read through caches that can be stale."""
    object_storage = FileService(Path("tenant/project"), local_filesystem=False)
    local_storage = FileService(tmp_path)

    assert local_promotion_confirmation_reader(object_storage) is None
    assert local_promotion_confirmation_reader(local_storage) is local_storage
