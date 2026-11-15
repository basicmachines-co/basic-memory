"""Tests for note-content reconciliation service behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from basic_memory import db, file_utils
from basic_memory.indexing.note_content_reconciliation import (
    NoteContentMaterializationStatusUpdate,
    NoteContentMaterializedCurrent,
    NoteContentReconciliationAnchor,
    NoteContentState,
)
from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconciler,
    RepositoryNoteMaterializationFailureMarker,
    apply_note_content_update_plan,
    note_content_repository_for_project,
    reconcile_note_content_for_entity,
)
from basic_memory.indexing.storage_note_changes import (
    ACCEPTED_STORAGE_CHANGE_SOURCE,
    StorageNoteChangeJournal,
)
from basic_memory.models import AcceptedProjectNoteChange, Entity, NoteContent, Project
from basic_memory.repository import NoteContentRepository
from basic_memory.repository.note_content_repository import AcceptedNoteContentVersions
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)


class FakeSession:
    """Minimal async session surface used by the reconciler conflict path."""

    def __init__(self) -> None:
        self.rollback_count = 0
        self.get = AsyncMock(return_value=SimpleNamespace(is_markdown=True))

    async def rollback(self) -> None:
        self.rollback_count += 1


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class RecordingChangeJournal:
    """Record each journal write the reconciler asks for, without a database."""

    def __init__(self) -> None:
        self.recorded: list[dict[str, object]] = []

    async def record_storage_note_version(
        self,
        session: object,
        *,
        project_id: int,
        entity_id: int,
        first_version: bool,
    ) -> None:
        self.recorded.append(
            {
                "session": session,
                "project_id": project_id,
                "entity_id": entity_id,
                "first_version": first_version,
            }
        )


class FakeRepositorySession(FakeSession):
    async def __aenter__(self) -> FakeRepositorySession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()


@pytest.mark.asyncio
async def test_reconciler_converges_after_concurrent_create_conflict() -> None:
    """A concurrent repair winner should not make the losing worker fail permanently."""
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    markdown_content = "# Repaired\n"
    observed_checksum = await file_utils.compute_checksum(markdown_content)
    existing_note_content = SimpleNamespace(
        project_id=7,
        db_version=1,
        db_checksum="previous-file-checksum",
        file_version=1,
        file_checksum="previous-file-checksum",
        file_write_status="synced",
        file_path="notes/repaired.md",
    )
    journal = RecordingChangeJournal()
    repository = SimpleNamespace(
        get_by_entity_id=AsyncMock(side_effect=[None, existing_note_content]),
        create=AsyncMock(
            side_effect=IntegrityError(
                "INSERT INTO note_content",
                {},
                Exception("duplicate key"),
            )
        ),
        update_state_fields=AsyncMock(),
        find_accepted_content_versions=AsyncMock(
            return_value=AcceptedNoteContentVersions(
                newest_db_version=None,
                matched_db_version=None,
            )
        ),
    )
    entity = cast(Entity, SimpleNamespace(id=42))
    session = FakeSession()

    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object):
        yield session

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_content_reconciler.db.scoped_session",
            fake_scoped_session,
        )
        outcome = await NoteContentReconciler(
            note_content_repository=cast(Any, repository),
            session_maker=cast(Any, object()),
            change_journal=journal,
        ).reconcile(
            entity=entity,
            markdown_content=markdown_content,
            observed_at=observed_at,
            source="read_repair",
        )

    repository.create.assert_awaited_once()
    assert outcome.status == "current"
    assert outcome.generation == 2
    assert session.rollback_count == 1
    assert repository.get_by_entity_id.await_count == 2
    # The losing bootstrap journals nothing; only the promotion that follows does.
    assert journal.recorded == [
        {"session": session, "project_id": 7, "entity_id": 42, "first_version": False}
    ]
    repository.update_state_fields.assert_awaited_once_with(
        session,
        42,
        expected_db_version=1,
        markdown_content=markdown_content,
        db_version=2,
        db_checksum=observed_checksum,
        file_version=2,
        file_checksum=observed_checksum,
        file_write_status="synced",
        last_source="read_repair",
        updated_at=observed_at,
        file_updated_at=observed_at,
        last_materialization_error=None,
        last_materialization_attempt_at=None,
    )


@pytest.mark.asyncio
async def test_reconciler_does_not_bootstrap_reclassified_resource() -> None:
    """A resource reclassification that wins the Entity lock blocks bootstrap."""
    repository = SimpleNamespace(
        get_by_entity_id=AsyncMock(return_value=None),
        create=AsyncMock(),
        update_state_fields=AsyncMock(),
    )
    session = FakeSession()
    session.get.return_value = SimpleNamespace(is_markdown=False)
    journal = RecordingChangeJournal()

    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object):
        yield session

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_content_reconciler.db.scoped_session",
            fake_scoped_session,
        )
        outcome = await NoteContentReconciler(
            note_content_repository=cast(Any, repository),
            session_maker=cast(Any, object()),
            change_journal=journal,
        ).reconcile(
            entity=cast(Entity, SimpleNamespace(id=42)),
            markdown_content="# Stale Markdown\n",
            observed_at=datetime(2026, 4, 13, 15, 0, tzinfo=UTC),
            source="file_indexer",
        )

    session.get.assert_awaited_once_with(Entity, 42, with_for_update=True)
    repository.create.assert_not_awaited()
    assert outcome.status == "stale"
    assert outcome.generation is None
    assert journal.recorded == []


@pytest.mark.asyncio
async def test_reconciler_skips_stale_plan_when_version_guard_loses() -> None:
    """A concurrent accepted write (guard returns None) makes the reconcile a benign no-op."""
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    markdown_content = "# Newly observed on disk\n"
    stale_checksum = await file_utils.compute_checksum("# Older accepted content\n")
    existing_note_content = SimpleNamespace(
        db_version=3,
        db_checksum=stale_checksum,
        file_version=3,
        file_checksum=stale_checksum,
        file_write_status="synced",
        file_path="notes/observed.md",
    )
    repository = SimpleNamespace(
        get_by_entity_id=AsyncMock(return_value=existing_note_content),
        create=AsyncMock(),
        # update_state_fields returns None: the db_version advanced under us.
        update_state_fields=AsyncMock(return_value=None),
        find_accepted_content_versions=AsyncMock(
            return_value=AcceptedNoteContentVersions(
                newest_db_version=None,
                matched_db_version=None,
            )
        ),
    )
    entity = cast(Entity, SimpleNamespace(id=42))
    session = FakeSession()
    journal = RecordingChangeJournal()

    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object):
        yield session

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_content_reconciler.db.scoped_session",
            fake_scoped_session,
        )
        # Must not raise even though the guarded write was skipped.
        outcome = await NoteContentReconciler(
            note_content_repository=cast(Any, repository),
            session_maker=cast(Any, object()),
            change_journal=journal,
        ).reconcile(
            entity=entity,
            markdown_content=markdown_content,
            observed_at=observed_at,
            source="file_indexer",
        )

    repository.create.assert_not_awaited()
    assert outcome.status == "stale"
    assert outcome.generation is None
    assert repository.update_state_fields.await_count == 1
    _, kwargs = repository.update_state_fields.await_args
    assert kwargs["expected_db_version"] == 3
    assert journal.recorded == []


@pytest.mark.asyncio
async def test_reconciler_skips_observation_when_anchor_changed_during_indexing() -> None:
    """A file read against version N cannot promote after an accepted write advances to N+1."""
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    accepted_checksum = await file_utils.compute_checksum("# MCP winner\n")
    repository = SimpleNamespace(
        get_by_entity_id=AsyncMock(
            return_value=SimpleNamespace(
                db_version=4,
                db_checksum=accepted_checksum,
                file_version=3,
                file_checksum="old-file-checksum",
                file_write_status="pending",
            )
        ),
        create=AsyncMock(),
        update_state_fields=AsyncMock(),
    )
    entity = cast(Entity, SimpleNamespace(id=42))

    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object):
        yield FakeSession()

    anchor = NoteContentReconciliationAnchor(
        entity_id=42,
        state=NoteContentState(
            db_version=3,
            db_checksum="old-file-checksum",
            file_version=3,
            file_checksum="old-file-checksum",
            file_write_status="synced",
        ),
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_content_reconciler.db.scoped_session",
            fake_scoped_session,
        )
        outcome = await NoteContentReconciler(
            note_content_repository=cast(Any, repository),
            session_maker=cast(Any, object()),
        ).reconcile(
            entity=entity,
            markdown_content="# Stale file snapshot\n",
            observed_at=observed_at,
            source="file_indexer",
            anchor=anchor,
        )

    repository.create.assert_not_awaited()
    repository.update_state_fields.assert_not_awaited()
    assert outcome.status == "stale"
    assert outcome.generation is None


@pytest.mark.asyncio
async def test_reconciler_treats_initial_absence_as_stale_when_content_appears() -> None:
    """An accepted write that creates note_content during indexing must win."""
    accepted_checksum = await file_utils.compute_checksum("# Accepted write\n")
    repository = SimpleNamespace(
        get_by_entity_id=AsyncMock(
            return_value=SimpleNamespace(
                db_version=1,
                db_checksum=accepted_checksum,
                file_version=1,
                file_checksum=accepted_checksum,
                file_write_status="synced",
            )
        ),
        create=AsyncMock(),
        update_state_fields=AsyncMock(),
    )
    entity = cast(Entity, SimpleNamespace(id=42))

    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object):
        yield FakeSession()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_content_reconciler.db.scoped_session",
            fake_scoped_session,
        )
        outcome = await NoteContentReconciler(
            note_content_repository=cast(Any, repository),
            session_maker=cast(Any, object()),
        ).reconcile(
            entity=entity,
            markdown_content="# Stale initial file\n",
            observed_at=datetime(2026, 4, 13, 15, 0, tzinfo=UTC),
            source="file_indexer",
            anchor=NoteContentReconciliationAnchor(entity_id=None, state=None),
        )

    assert outcome.status == "stale"
    assert outcome.generation is None
    repository.create.assert_not_awaited()
    repository.update_state_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_defers_unrelated_file_while_materialization_is_pending() -> None:
    """An unanchored repair cannot turn a third checksum into a revision while DB is ahead."""
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    accepted_checksum = await file_utils.compute_checksum("# MCP winner\n")
    repository = SimpleNamespace(
        get_by_entity_id=AsyncMock(
            return_value=SimpleNamespace(
                db_version=4,
                db_checksum=accepted_checksum,
                file_version=3,
                file_checksum="old-file-checksum",
                file_write_status="pending",
            )
        ),
        create=AsyncMock(),
        update_state_fields=AsyncMock(),
    )
    entity = cast(Entity, SimpleNamespace(id=42))

    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object):
        yield FakeSession()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_content_reconciler.db.scoped_session",
            fake_scoped_session,
        )
        outcome = await NoteContentReconciler(
            note_content_repository=cast(Any, repository),
            session_maker=cast(Any, object()),
        ).reconcile(
            entity=entity,
            markdown_content="# Unrelated file snapshot\n",
            observed_at=observed_at,
            source="read_repair",
        )

    repository.create.assert_not_awaited()
    repository.update_state_fields.assert_not_awaited()
    assert outcome.status == "deferred"
    assert outcome.generation is None


@pytest.mark.asyncio
async def test_reconciler_does_not_claim_newer_db_generation_for_older_file() -> None:
    """File bookkeeping may advance without lending DB generation 9 to version 8 bytes."""
    older_content = "# Older materialized file\n"
    older_checksum = await file_utils.compute_checksum(older_content)
    repository = SimpleNamespace(
        get_by_entity_id=AsyncMock(
            return_value=SimpleNamespace(
                db_version=9,
                db_checksum="new-db-checksum",
                file_version=8,
                file_checksum=older_checksum,
                file_write_status="pending",
            )
        ),
        create=AsyncMock(),
        update_state_fields=AsyncMock(return_value=SimpleNamespace()),
    )

    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object):
        yield FakeSession()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_content_reconciler.db.scoped_session",
            fake_scoped_session,
        )
        outcome = await NoteContentReconciler(
            note_content_repository=cast(Any, repository),
            session_maker=cast(Any, object()),
        ).reconcile(
            entity=cast(Entity, SimpleNamespace(id=42)),
            markdown_content=older_content,
            observed_at=datetime(2026, 4, 13, 15, 0, tzinfo=UTC),
            source="index",
        )

    assert outcome.status == "deferred"
    assert outcome.generation is None
    repository.update_state_fields.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_note_content_update_plan_publishes_materialized_current_file() -> None:
    """Materialization publish plans should apply through the note_content repository."""
    file_updated_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    attempted_at = datetime(2026, 4, 13, 14, 59, tzinfo=UTC)
    repository = SimpleNamespace(update_state_fields=AsyncMock())
    session = FakeSession()

    await apply_note_content_update_plan(
        cast(Any, repository),
        cast(Any, session),
        42,
        NoteContentMaterializedCurrent(
            file_version=4,
            file_checksum="written-file-checksum",
            file_write_status="synced",
            file_updated_at=file_updated_at,
            last_materialization_error=None,
            last_materialization_attempt_at=attempted_at,
        ),
    )

    repository.update_state_fields.assert_awaited_once_with(
        session,
        42,
        expected_db_version=None,
        file_version=4,
        file_checksum="written-file-checksum",
        file_write_status="synced",
        file_updated_at=file_updated_at,
        last_materialization_error=None,
        last_materialization_attempt_at=attempted_at,
    )


@pytest.mark.asyncio
async def test_apply_note_content_update_plan_marks_materialization_status() -> None:
    """Materialization status plans should apply without overwriting file checksum."""
    attempted_at = datetime(2026, 4, 13, 14, 59, tzinfo=UTC)
    repository = SimpleNamespace(update_state_fields=AsyncMock())
    session = FakeSession()

    await apply_note_content_update_plan(
        cast(Any, repository),
        cast(Any, session),
        42,
        NoteContentMaterializationStatusUpdate(
            file_write_status="failed",
            file_checksum=None,
            last_materialization_error="write failed",
            last_materialization_attempt_at=attempted_at,
        ),
    )

    repository.update_state_fields.assert_awaited_once_with(
        session,
        42,
        expected_db_version=None,
        file_write_status="failed",
        last_materialization_error="write failed",
        last_materialization_attempt_at=attempted_at,
    )


@pytest.mark.asyncio
async def test_repository_failure_marker_records_materialization_enqueue_failure() -> None:
    """The failure-marker protocol adapter should be usable by enqueue runners."""
    session = FakeRepositorySession()
    repository_calls: list[tuple[int, int, dict[str, object]]] = []

    def session_maker() -> FakeRepositorySession:
        return session

    class FakeNoteContentRepository:
        def __init__(self, project_id: int) -> None:
            self.project_id = project_id

        async def update_state_fields(
            self,
            session: Any,
            entity_id: int,
            **updates: object,
        ) -> None:
            assert session is not None
            repository_calls.append((self.project_id, entity_id, updates))

    marker = RepositoryNoteMaterializationFailureMarker(
        session_maker=cast(Any, session_maker),
        note_content_store=lambda project_id: cast(Any, FakeNoteContentRepository(project_id)),
    )

    await marker.mark_note_materialization_failed(
        project_id=7,
        entity_id=42,
        error_message="pgq unavailable",
    )

    assert len(repository_calls) == 1
    project_id, entity_id, updates = repository_calls[0]
    assert project_id == 7
    assert entity_id == 42
    assert updates["file_write_status"] == "failed"
    assert updates["last_materialization_error"] == "pgq unavailable"
    assert isinstance(updates["last_materialization_attempt_at"], datetime)


def test_failure_marker_defaults_to_core_note_content_repository() -> None:
    """The default materialization contract should stay backed by core repositories."""
    marker = RepositoryNoteMaterializationFailureMarker(session_maker=cast(Any, object()))

    assert marker.note_content_store is note_content_repository_for_project
    assert isinstance(note_content_repository_for_project(7), NoteContentRepository)


@pytest.mark.asyncio
async def test_reconcile_note_content_for_entity_reads_canonical_file() -> None:
    """Entity-id reconciliation should read canonical markdown and delegate to reconciler."""
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    session = FakeRepositorySession()
    entity = SimpleNamespace(id=42, file_path="notes/indexed.md", is_markdown=True)
    repository = SimpleNamespace(find_by_id=AsyncMock(return_value=entity))
    file_reader = SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                content=b"# Indexed\n",
                last_modified=observed_at,
            )
        )
    )
    reconciler = SimpleNamespace(reconcile=AsyncMock())

    def session_maker() -> FakeRepositorySession:
        return session

    reconciled = await reconcile_note_content_for_entity(
        session_maker=cast(Any, session_maker),
        entity_repository=cast(Any, repository),
        file_reader=cast(Any, file_reader),
        reconciler=cast(Any, reconciler),
        entity_id=42,
        source="index",
    )

    assert reconciled is True
    repository.find_by_id.assert_awaited_once_with(session, 42)
    file_reader.get_file.assert_awaited_once_with("notes/indexed.md")
    reconciler.reconcile.assert_awaited_once_with(
        entity=entity,
        markdown_content="# Indexed\n",
        observed_at=observed_at,
        source="index",
    )


@pytest.mark.asyncio
async def test_reconcile_note_content_for_entity_skips_non_markdown_entities() -> None:
    """Non-markdown entities should not trigger a storage content read."""
    session = FakeRepositorySession()
    entity = SimpleNamespace(id=42, file_path="assets/image.png", is_markdown=False)
    repository = SimpleNamespace(find_by_id=AsyncMock(return_value=entity))
    file_reader = SimpleNamespace(get_file=AsyncMock())
    reconciler = SimpleNamespace(reconcile=AsyncMock())

    def session_maker() -> FakeRepositorySession:
        return session

    reconciled = await reconcile_note_content_for_entity(
        session_maker=cast(Any, session_maker),
        entity_repository=cast(Any, repository),
        file_reader=cast(Any, file_reader),
        reconciler=cast(Any, reconciler),
        entity_id=42,
        source="index",
    )

    assert reconciled is False
    repository.find_by_id.assert_awaited_once_with(session, 42)
    file_reader.get_file.assert_not_awaited()
    reconciler.reconcile.assert_not_awaited()


# --- Project change journal rows, against a real database ---

ACCEPTED_AT = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


async def _journal_rows(session_maker: Any, project: Project) -> list[AcceptedProjectNoteChange]:
    async with db.scoped_session(session_maker) as session:
        return list(await ProjectRepository().list_accepted_note_changes(session, project.id))


async def _partition_head(session_maker: Any, project: Project) -> int:
    async with db.scoped_session(session_maker) as session:
        head = await ProjectRepository().get_partition_position(session, project.id)
    assert head is not None
    return head


async def _stored_note_content(
    session_maker: Any, project: Project, entity: Entity
) -> NoteContent | None:
    async with db.scoped_session(session_maker) as session:
        return await NoteContentRepository(project_id=project.id).get_by_entity_id(
            session, entity.id
        )


async def _store_note_content(
    session_maker: Any,
    project: Project,
    entity: Entity,
    *,
    content: str,
    db_version: int,
    file_version: int | None = None,
    file_content: str | None = None,
    file_write_status: str = "synced",
) -> None:
    db_checksum = await file_utils.compute_checksum(content)
    file_checksum = await file_utils.compute_checksum(file_content or content)
    async with db.scoped_session(session_maker) as session:
        await NoteContentRepository(project_id=project.id).create(
            session,
            NoteContent(
                entity_id=entity.id,
                markdown_content=content,
                db_version=db_version,
                db_checksum=db_checksum,
                file_version=file_version if file_version is not None else db_version,
                file_checksum=file_checksum,
                file_write_status=file_write_status,
                last_source="api",
                updated_at=ACCEPTED_AT,
                file_updated_at=ACCEPTED_AT,
            ),
        )


def _reconciler(
    session_maker: Any,
    project: Project,
    *,
    change_journal: Any = None,
) -> NoteContentReconciler:
    return NoteContentReconciler(
        note_content_repository=NoteContentRepository(project_id=project.id),
        session_maker=session_maker,
        change_journal=change_journal,
    )


class FailingAfterJournalWrite:
    """Write the real journal row, then fail the transaction it belongs to."""

    async def record_storage_note_version(self, session: Any, **kwargs: Any) -> None:
        await StorageNoteChangeJournal().record_storage_note_version(session, **kwargs)
        raise RuntimeError("simulated failure after the journal write")


@pytest.mark.asyncio
async def test_bootstrap_journals_created_row_for_first_version(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    content = "# Created in storage\n"

    outcome = await _reconciler(session_maker, test_project).reconcile(
        entity=sample_entity,
        markdown_content=content,
        observed_at=OBSERVED_AT,
        source="index",
    )

    assert outcome.status == "current"
    assert outcome.generation == 1
    stored = await _stored_note_content(session_maker, test_project, sample_entity)
    assert stored is not None
    [row] = await _journal_rows(session_maker, test_project)
    assert (
        row.partition_position,
        row.operation,
        row.entity_id,
        row.note_external_id,
        row.permalink,
        row.title,
        row.file_path,
        row.previous_file_path,
        row.db_version,
        row.db_checksum,
        row.source,
        row.actor_kind,
    ) == (
        1,
        "created",
        sample_entity.id,
        sample_entity.external_id,
        sample_entity.permalink,
        sample_entity.title,
        sample_entity.file_path,
        None,
        stored.db_version,
        stored.db_checksum,
        ACCEPTED_STORAGE_CHANGE_SOURCE,
        None,
    )
    assert row.materialized_at is not None
    assert row.materialized_at == row.accepted_at


@pytest.mark.asyncio
async def test_bootstrap_of_journaled_note_records_updated(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    """A lost and rebuilt note_content row never gives the note a second `created` row."""
    repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        position = await repository.advance_partition_position(session, test_project.id)
        await repository.record_accepted_note_change(
            session,
            RuntimeAcceptedProjectNoteChange(
                project_id=test_project.id,
                project_external_id=test_project.external_id,
                partition_position=position,
                entity_id=sample_entity.id,
                note_external_id=sample_entity.external_id,
                permalink=str(sample_entity.permalink),
                title=sample_entity.title,
                operation=RuntimeProjectNoteOperation.created,
                file_path=sample_entity.file_path,
                accepted_at=ACCEPTED_AT,
                source="api",
                db_version=1,
                db_checksum="api-checksum",
            ),
        )

    await _reconciler(session_maker, test_project).reconcile(
        entity=sample_entity,
        markdown_content="# Rebuilt from storage\n",
        observed_at=OBSERVED_AT,
        source="index",
    )

    assert [row.operation for row in await _journal_rows(session_maker, test_project)] == [
        "created",
        "updated",
    ]


@pytest.mark.asyncio
async def test_promotion_journals_updated_row_with_new_version(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    await _store_note_content(
        session_maker, test_project, sample_entity, content="# First\n", db_version=1
    )

    outcome = await _reconciler(session_maker, test_project).reconcile(
        entity=sample_entity,
        markdown_content="# Edited in storage\n",
        observed_at=OBSERVED_AT,
        source="index",
    )

    assert outcome.status == "current"
    assert outcome.generation == 2
    stored = await _stored_note_content(session_maker, test_project, sample_entity)
    assert stored is not None
    [row] = await _journal_rows(session_maker, test_project)
    assert (row.operation, row.db_version, row.db_checksum, row.source) == (
        "updated",
        2,
        stored.db_checksum,
        ACCEPTED_STORAGE_CHANGE_SOURCE,
    )
    assert stored.db_checksum == await file_utils.compute_checksum("# Edited in storage\n")
    assert row.materialized_at == row.accepted_at


@pytest.mark.asyncio
async def test_file_already_in_sync_writes_no_journal_row(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    await _store_note_content(
        session_maker, test_project, sample_entity, content="# In sync\n", db_version=3
    )

    outcome = await _reconciler(session_maker, test_project).reconcile(
        entity=sample_entity,
        markdown_content="# In sync\n",
        observed_at=OBSERVED_AT,
        source="index",
    )

    assert (outcome.status, outcome.generation) == ("current", 3)
    assert await _journal_rows(session_maker, test_project) == []
    assert await _partition_head(session_maker, test_project) == 0


@pytest.mark.asyncio
async def test_older_materialized_file_writes_no_journal_row(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    """Bytes of the previous file version update bookkeeping only."""
    await _store_note_content(
        session_maker,
        test_project,
        sample_entity,
        content="# Accepted v4\n",
        db_version=4,
        file_version=3,
        file_content="# Materialized v3\n",
        file_write_status="pending",
    )

    outcome = await _reconciler(session_maker, test_project).reconcile(
        entity=sample_entity,
        markdown_content="# Materialized v3\n",
        observed_at=OBSERVED_AT,
        source="index",
    )

    assert outcome.status == "deferred"
    assert await _journal_rows(session_maker, test_project) == []
    assert await _partition_head(session_maker, test_project) == 0


@pytest.mark.asyncio
async def test_deferred_promotion_writes_no_journal_row(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    """Unrelated bytes cannot become a version while an accepted write is still pending."""
    await _store_note_content(
        session_maker,
        test_project,
        sample_entity,
        content="# Accepted v4\n",
        db_version=4,
        file_version=3,
        file_content="# Materialized v3\n",
        file_write_status="pending",
    )

    outcome = await _reconciler(session_maker, test_project).reconcile(
        entity=sample_entity,
        markdown_content="# Unrelated storage bytes\n",
        observed_at=OBSERVED_AT,
        source="index",
    )

    assert outcome.status == "deferred"
    stored = await _stored_note_content(session_maker, test_project, sample_entity)
    assert stored is not None
    assert stored.db_version == 4
    assert await _journal_rows(session_maker, test_project) == []
    assert await _partition_head(session_maker, test_project) == 0


@pytest.mark.asyncio
async def test_stale_anchor_writes_no_journal_row(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    """A file read before an accepted write landed cannot claim a version or a row."""
    await _store_note_content(
        session_maker, test_project, sample_entity, content="# Accepted\n", db_version=2
    )

    outcome = await _reconciler(session_maker, test_project).reconcile(
        entity=sample_entity,
        markdown_content="# Read before the accepted write\n",
        observed_at=OBSERVED_AT,
        source="index",
        anchor=NoteContentReconciliationAnchor(entity_id=sample_entity.id, state=None),
    )

    assert outcome.status == "stale"
    assert await _journal_rows(session_maker, test_project) == []
    assert await _partition_head(session_maker, test_project) == 0


@pytest.mark.asyncio
async def test_failed_bootstrap_transaction_keeps_neither_content_nor_journal_row(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    with pytest.raises(RuntimeError, match="simulated failure after the journal write"):
        await _reconciler(
            session_maker, test_project, change_journal=FailingAfterJournalWrite()
        ).reconcile(
            entity=sample_entity,
            markdown_content="# Never committed\n",
            observed_at=OBSERVED_AT,
            source="index",
        )

    assert await _stored_note_content(session_maker, test_project, sample_entity) is None
    assert await _journal_rows(session_maker, test_project) == []
    assert await _partition_head(session_maker, test_project) == 0


@pytest.mark.asyncio
async def test_failed_promotion_transaction_keeps_neither_version_nor_journal_row(
    session_maker: Any, test_project: Project, sample_entity: Entity
) -> None:
    await _store_note_content(
        session_maker, test_project, sample_entity, content="# First\n", db_version=1
    )

    with pytest.raises(RuntimeError, match="simulated failure after the journal write"):
        await _reconciler(
            session_maker, test_project, change_journal=FailingAfterJournalWrite()
        ).reconcile(
            entity=sample_entity,
            markdown_content="# Never committed\n",
            observed_at=OBSERVED_AT,
            source="index",
        )

    stored = await _stored_note_content(session_maker, test_project, sample_entity)
    assert stored is not None
    assert (stored.db_version, stored.markdown_content) == (1, "# First\n")
    assert await _journal_rows(session_maker, test_project) == []
    assert await _partition_head(session_maker, test_project) == 0
