"""Tests for portable note materialization orchestration."""

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace, TracebackType
from typing import cast
from contextlib import AbstractAsyncContextManager
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.note_materialization_runner import (
    ContentStoreNoteMaterializationFileWriter,
    NoteMaterializationPreflightResult,
    NoteMaterializationPublishAction,
    NoteMaterializationStatusPublication,
    RepositoryNoteMaterializationPreflight,
    RepositoryNoteMaterializationPublisher,
    RepositoryNoteMaterializationStatusPublisher,
    plan_note_materialization_preflight,
    plan_written_note_materialization_publish,
    run_note_materialization,
)
from basic_memory.indexing.note_content_reconciliation import NoteContentState
from basic_memory.models import Entity, NoteContent
from basic_memory.runtime.cleanup import RuntimeNoteFileDeleteJobRequest
from basic_memory.runtime.note_content import (
    RuntimeFileConflict,
    RuntimeFileConflictError,
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteFileDelete,
)
from basic_memory.runtime.note_materialization import (
    RuntimePreparedNoteWrite,
    RuntimeWrittenFileState,
    plan_prepared_note_write,
)
from basic_memory.file_utils import FileError
from basic_memory.services.exceptions import FileOperationError


class FakePreflight:
    def __init__(self, result: NoteMaterializationPreflightResult) -> None:
        self.result = result
        self.requests: list[RuntimeNoteMaterializationJobRequest] = []

    async def prepare_note_materialization(
        self,
        request: RuntimeNoteMaterializationJobRequest,
    ) -> NoteMaterializationPreflightResult:
        self.requests.append(request)
        return self.result


class FakeWriter:
    def __init__(
        self,
        written_file: RuntimeWrittenFileState | None = None,
        *,
        error: RuntimeFileConflictError | FileOperationError | FileError | OSError | None = None,
    ) -> None:
        self.written_file = written_file
        self.error = error
        self.prepared_writes: list[RuntimePreparedNoteWrite] = []

    async def write_prepared_note(
        self,
        prepared_write: RuntimePreparedNoteWrite,
    ) -> RuntimeWrittenFileState:
        self.prepared_writes.append(prepared_write)
        if self.error is not None:
            raise self.error
        if self.written_file is None:
            raise AssertionError("written_file is required when no error is configured")
        return self.written_file


class FakePublisher:
    def __init__(self, result: RuntimeNoteMaterializationResult) -> None:
        self.result = result
        self.calls: list[
            tuple[
                RuntimeNoteMaterializationJobRequest,
                RuntimePreparedNoteWrite,
                RuntimeWrittenFileState,
            ]
        ] = []

    async def publish_written_file_state(
        self,
        request: RuntimeNoteMaterializationJobRequest,
        prepared_write: RuntimePreparedNoteWrite,
        written_file: RuntimeWrittenFileState,
    ) -> RuntimeNoteMaterializationResult:
        self.calls.append((request, prepared_write, written_file))
        return self.result


class FakeStatusPublisher:
    def __init__(self) -> None:
        self.calls: list[
            tuple[RuntimeNoteMaterializationJobRequest, NoteMaterializationStatusPublication]
        ] = []

    async def publish_note_materialization_status(
        self,
        request: RuntimeNoteMaterializationJobRequest,
        publication: NoteMaterializationStatusPublication,
    ) -> None:
        self.calls.append((request, publication))


class FakeCleanupEnqueuer:
    def __init__(self) -> None:
        self.requests: list[RuntimeNoteFileDeleteJobRequest] = []

    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None:
        self.requests.append(request)


class FakeSessionLock:
    def __init__(self) -> None:
        self.calls: list[tuple[AsyncSession, int, int]] = []

    async def lock_note_materialization(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        entity_id: int,
    ) -> None:
        self.calls.append((session, project_id, entity_id))


class FakeRepositorySession:
    def __init__(self, *, entity: Entity | None, note_content: NoteContent | None) -> None:
        self.entity = entity
        self.note_content = note_content
        self.flush_count = 0

    async def get(self, model: type[object], identity: int) -> object | None:
        assert identity == 42
        if model is Entity:
            return self.entity
        if model is NoteContent:
            return self.note_content
        raise AssertionError(f"unexpected model: {model}")

    async def flush(self) -> None:
        self.flush_count += 1


class FakeScopedSession:
    def __init__(self, session: FakeRepositorySession) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncSession:
        return cast(AsyncSession, self.session)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return None


@dataclass(slots=True)
class RecordingScopedSession:
    """Stand-in for ``db.scoped_session`` that yields a fake session and records opens."""

    scoped_session: FakeScopedSession
    opened_session_makers: list[async_sessionmaker[AsyncSession]]

    def __call__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AbstractAsyncContextManager[AsyncSession]:
        self.opened_session_makers.append(session_maker)
        return self.scoped_session


class RecordingNoteContentRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[AsyncSession, int, dict[str, object]]] = []

    async def update_state_fields(
        self,
        session: AsyncSession,
        entity_id: int,
        **updates: object,
    ) -> NoteContent | None:
        self.calls.append((session, entity_id, updates))
        # Return a non-None row to signal the update landed; apply_note_content_
        # update_plan reads this as "applied" (None means the version-guard lost).
        return NoteContent(entity_id=entity_id)

    async def get_by_entity_id(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> NoteContent | None:
        raise AssertionError("repository adapter tests should not load note_content")

    async def create(
        self,
        session: AsyncSession,
        data: NoteContent,
    ) -> NoteContent:
        raise AssertionError("repository adapter tests should not create note_content")


@dataclass(frozen=True, slots=True)
class FakeFileMetadata:
    modified_at: datetime


class FakeContentStore:
    def __init__(self, *, modified_at: datetime) -> None:
        self.modified_at = modified_at
        self.write_calls: list[tuple[str, str, dict[str, str] | None]] = []

    async def exists(self, path: str) -> bool:
        return False

    async def compute_checksum(self, path: str) -> str:
        raise AssertionError("missing-file materialization should not compute checksum")

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> str:
        self.write_calls.append((path, content, metadata))
        return "new-file-sum"

    async def get_file_metadata(self, path: str) -> FakeFileMetadata:
        return FakeFileMetadata(modified_at=self.modified_at)


def materialization_request() -> RuntimeNoteMaterializationJobRequest:
    return RuntimeNoteMaterializationJobRequest(
        project_id=7,
        entity_id=42,
        db_version=4,
        db_checksum="db-checksum",
        actor_user_profile_id=UUID("33333333-3333-3333-3333-333333333333"),
        actor_kind="mcp_client",
        actor_name="Claude Code",
        source="mcp",
        cleanup_file_path="notes/old.md",
        cleanup_file_checksum="old-cleanup-sum",
    )


def prepared_write(
    request: RuntimeNoteMaterializationJobRequest,
) -> RuntimePreparedNoteWrite:
    return plan_prepared_note_write(
        request=request,
        file_path="notes/a.md",
        markdown_content="# A note\n",
        previous_file_checksum="old-file-sum",
        attempted_at=datetime(2026, 6, 18, 14, 17, tzinfo=UTC),
    )


def written_file() -> RuntimeWrittenFileState:
    return RuntimeWrittenFileState(
        file_path="notes/a.md",
        file_checksum="new-file-sum",
        file_updated_at=datetime(2026, 6, 18, 14, 18, tzinfo=UTC),
    )


def note_content_state(
    *,
    db_version: int = 4,
    db_checksum: str = "db-checksum",
    file_version: int | None = None,
    file_checksum: str | None = "old-file-sum",
) -> NoteContentState:
    return NoteContentState(
        db_version=db_version,
        db_checksum=db_checksum,
        file_version=file_version,
        file_checksum=file_checksum,
    )


def materialization_entity(*, file_path: str = "notes/a.md") -> Entity:
    return Entity(
        id=42,
        project_id=7,
        title="A note",
        note_type="note",
        content_type="text/markdown",
        file_path=file_path,
        checksum="old-file-sum",
        created_at=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
        updated_at=datetime(2024, 1, 16, 11, 45, tzinfo=UTC),
    )


def materialization_note_content(
    *,
    file_path: str = "notes/a.md",
    db_version: int = 4,
    db_checksum: str = "db-checksum",
    file_checksum: str | None = "old-file-sum",
) -> NoteContent:
    return NoteContent(
        entity_id=42,
        project_id=7,
        external_id="note-42",
        file_path=file_path,
        markdown_content="# A note\n",
        db_version=db_version,
        db_checksum=db_checksum,
        file_version=3,
        file_checksum=file_checksum,
        file_write_status="pending",
    )


@pytest.mark.asyncio
async def test_content_store_note_materialization_file_writer_writes_prepared_note() -> None:
    """The portable writer adapter should not need a cloud-specific wrapper."""
    request = materialization_request()
    prepared = plan_prepared_note_write(
        request=request,
        file_path="notes/a.md",
        markdown_content="# A note\n",
        previous_file_checksum=None,
        attempted_at=datetime(2026, 6, 18, 14, 17, tzinfo=UTC),
    )
    modified_at = datetime(2026, 6, 18, 14, 18, tzinfo=UTC)
    content_store = FakeContentStore(modified_at=modified_at)

    written = await ContentStoreNoteMaterializationFileWriter(
        content_store=content_store
    ).write_prepared_note(prepared)

    assert written == RuntimeWrittenFileState(
        file_path="notes/a.md",
        file_checksum="new-file-sum",
        file_updated_at=modified_at,
    )
    assert content_store.write_calls == [
        (
            "notes/a.md",
            "# A note\n",
            prepared.object_metadata.to_storage_metadata(),
        )
    ]


def test_plan_note_materialization_preflight_returns_missing_terminal_result() -> None:
    request = materialization_request()

    result = plan_note_materialization_preflight(
        request,
        entity=None,
        note_content=None,
        attempted_at=datetime(2026, 6, 18, 14, 17, tzinfo=UTC),
    )

    assert result == NoteMaterializationPreflightResult.terminal(
        RuntimeNoteMaterializationResult(
            entity_id=42,
            status=RuntimeNoteMaterializationStatus.missing,
            reason="note state no longer exists: 42",
        ),
        cleanup_file=RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/old.md",
            file_checksum="old-cleanup-sum",
        ),
    )


def test_plan_note_materialization_preflight_returns_stale_terminal_result() -> None:
    request = materialization_request()

    result = plan_note_materialization_preflight(
        request,
        entity=SimpleNamespace(file_path="notes/a.md"),
        note_content=SimpleNamespace(
            db_version=5,
            db_checksum="newer-db-checksum",
            markdown_content="# Newer\n",
            file_checksum="old-file-sum",
        ),
        attempted_at=datetime(2026, 6, 18, 14, 17, tzinfo=UTC),
    )

    assert result == NoteMaterializationPreflightResult.terminal(
        RuntimeNoteMaterializationResult(
            entity_id=42,
            status=RuntimeNoteMaterializationStatus.stale,
            reason="accepted note changed before file write: 42",
            file_path="notes/a.md",
        )
    )


def test_plan_note_materialization_preflight_returns_prepared_write() -> None:
    request = materialization_request()
    attempted_at = datetime(2026, 6, 18, 14, 17, tzinfo=UTC)

    result = plan_note_materialization_preflight(
        request,
        entity=SimpleNamespace(file_path="notes/a.md"),
        note_content=SimpleNamespace(
            db_version=4,
            db_checksum="db-checksum",
            markdown_content="# A note\n",
            file_checksum="old-file-sum",
        ),
        attempted_at=attempted_at,
    )

    assert result == NoteMaterializationPreflightResult.prepared(
        plan_prepared_note_write(
            request=request,
            file_path="notes/a.md",
            markdown_content="# A note\n",
            previous_file_checksum="old-file-sum",
            attempted_at=attempted_at,
        )
    )


@pytest.mark.asyncio
async def test_repository_note_materialization_preflight_marks_current_note_writing() -> None:
    request = materialization_request()
    attempted_at = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
    entity = materialization_entity()
    note_content = materialization_note_content()
    session = FakeRepositorySession(entity=entity, note_content=note_content)
    session_lock = FakeSessionLock()
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    scoped_session = RecordingScopedSession(
        scoped_session=FakeScopedSession(session),
        opened_session_makers=[],
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_materialization_runner.db.scoped_session",
            scoped_session,
        )
        monkeypatch.setattr(
            "basic_memory.indexing.note_materialization_runner.note_materialization_utc_now",
            lambda: attempted_at,
        )
        result = await RepositoryNoteMaterializationPreflight(
            session_maker=session_maker,
            session_lock=session_lock,
        ).prepare_note_materialization(request)

    assert result == NoteMaterializationPreflightResult.prepared(
        plan_prepared_note_write(
            request=request,
            file_path="notes/a.md",
            markdown_content="# A note\n",
            previous_file_checksum="old-file-sum",
            attempted_at=attempted_at,
        )
    )
    assert session_lock.calls == [(cast(AsyncSession, session), 7, 42)]
    assert note_content.file_write_status == "writing"
    assert note_content.last_materialization_attempt_at == attempted_at
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_repository_note_materialization_preflight_uses_scoped_session_and_now() -> None:
    request = materialization_request()
    attempted_at = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
    entity = materialization_entity()
    note_content = materialization_note_content()
    session = FakeRepositorySession(entity=entity, note_content=note_content)
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    scoped_session = RecordingScopedSession(
        scoped_session=FakeScopedSession(session),
        opened_session_makers=[],
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_materialization_runner.db.scoped_session",
            scoped_session,
        )
        monkeypatch.setattr(
            "basic_memory.indexing.note_materialization_runner.note_materialization_utc_now",
            lambda: attempted_at,
        )
        result = await RepositoryNoteMaterializationPreflight(
            session_maker=session_maker,
        ).prepare_note_materialization(request)

    assert result.require_prepared_write().attempted_at == attempted_at
    assert scoped_session.opened_session_makers == [session_maker]


@pytest.mark.asyncio
async def test_repository_note_materialization_publisher_updates_current_written_file() -> None:
    request = materialization_request()
    prepared = prepared_write(request)
    written = written_file()
    entity = materialization_entity()
    semantic_updated_at = entity.updated_at
    note_content = materialization_note_content()
    session = FakeRepositorySession(entity=entity, note_content=note_content)
    session_lock = FakeSessionLock()
    repository = RecordingNoteContentRepository()
    scoped_session = RecordingScopedSession(
        scoped_session=FakeScopedSession(session),
        opened_session_makers=[],
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_materialization_runner.db.scoped_session",
            scoped_session,
        )
        result = await RepositoryNoteMaterializationPublisher(
            session_maker=cast(async_sessionmaker[AsyncSession], object()),
            session_lock=session_lock,
            note_content_store=lambda project_id: repository,
        ).publish_written_file_state(request, prepared, written)

    assert result == RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.written,
        reason="note file written: notes/a.md",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
    )
    assert session_lock.calls == [(cast(AsyncSession, session), 7, 42)]
    assert repository.calls == [
        (
            cast(AsyncSession, session),
            42,
            {
                "expected_db_version": 4,
                "file_version": 4,
                "file_checksum": "new-file-sum",
                "file_write_status": "synced",
                "file_updated_at": written.file_updated_at,
                "last_materialization_error": None,
                "last_materialization_attempt_at": prepared.attempted_at,
            },
        )
    ]
    assert entity.updated_at == semantic_updated_at
    assert entity.mtime == written.file_updated_at.timestamp()
    assert entity.size == len(b"# A note\n")
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_repository_note_materialization_publisher_records_stale_written_file() -> None:
    """A newer accepted version at publish time records the written file as pending
    without touching the entity row; the newer version's own materialization owns
    the final state."""
    request = materialization_request()
    prepared = prepared_write(request)
    written = written_file()
    entity = materialization_entity()
    # The accepted note advanced past the requested db_version between the file
    # write and this publish.
    note_content = materialization_note_content(db_version=5, db_checksum="newer-db-checksum")
    session = FakeRepositorySession(entity=entity, note_content=note_content)
    session_lock = FakeSessionLock()
    repository = RecordingNoteContentRepository()
    scoped_session = RecordingScopedSession(
        scoped_session=FakeScopedSession(session),
        opened_session_makers=[],
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_materialization_runner.db.scoped_session",
            scoped_session,
        )
        result = await RepositoryNoteMaterializationPublisher(
            session_maker=cast(async_sessionmaker[AsyncSession], object()),
            session_lock=session_lock,
            note_content_store=lambda project_id: repository,
        ).publish_written_file_state(request, prepared, written)

    assert result == RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.stale,
        reason="file written but newer accepted note remains pending: 42",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
    )
    assert repository.calls == [
        (
            cast(AsyncSession, session),
            42,
            {
                "expected_db_version": 5,
                "file_version": 4,
                "file_checksum": "new-file-sum",
                "file_write_status": "pending",
                "file_updated_at": written.file_updated_at,
                "last_materialization_error": None,
                "last_materialization_attempt_at": prepared.attempted_at,
            },
        )
    ]
    # The entity metadata update belongs to the newer version's publish.
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_repository_note_materialization_status_publisher_records_conflict() -> None:
    request = materialization_request()
    attempted_at = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
    session = FakeRepositorySession(
        entity=materialization_entity(),
        note_content=materialization_note_content(),
    )
    session_lock = FakeSessionLock()
    repository = RecordingNoteContentRepository()
    scoped_session = RecordingScopedSession(
        scoped_session=FakeScopedSession(session),
        opened_session_makers=[],
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_materialization_runner.db.scoped_session",
            scoped_session,
        )
        await RepositoryNoteMaterializationStatusPublisher(
            session_maker=cast(async_sessionmaker[AsyncSession], object()),
            session_lock=session_lock,
            note_content_store=lambda project_id: repository,
        ).publish_note_materialization_status(
            request,
            NoteMaterializationStatusPublication(
                file_write_status="external_change_detected",
                attempted_at=attempted_at,
                actual_file_checksum="external-sum",
                error_message="Refusing to overwrite unexpected file",
            ),
        )

    assert session_lock.calls == [(cast(AsyncSession, session), 7, 42)]
    assert repository.calls == [
        (
            cast(AsyncSession, session),
            42,
            {
                "expected_db_version": None,
                "file_write_status": "external_change_detected",
                "last_materialization_error": "Refusing to overwrite unexpected file",
                "last_materialization_attempt_at": attempted_at,
                "file_checksum": "external-sum",
            },
        )
    ]


def test_plan_written_note_materialization_publish_handles_missing_note_content() -> None:
    request = materialization_request()

    plan = plan_written_note_materialization_publish(
        request=request,
        prepared_write=prepared_write(request),
        written_file=written_file(),
        current_note_content=None,
        current_file_path=None,
    )

    assert plan.action is NoteMaterializationPublishAction.missing_note_content
    assert plan.note_content_update is None
    assert plan.result == RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.missing,
        reason="note state disappeared after file write: 42",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
        written_file_orphaned=True,
    )


def test_plan_written_note_materialization_publish_handles_moved_note() -> None:
    request = materialization_request()

    plan = plan_written_note_materialization_publish(
        request=request,
        prepared_write=prepared_write(request),
        written_file=written_file(),
        current_note_content=note_content_state(),
        current_file_path="notes/moved.md",
    )

    assert plan.action is NoteMaterializationPublishAction.stale_file_path
    assert plan.note_content_update is None
    assert plan.result.status is RuntimeNoteMaterializationStatus.stale
    assert plan.result.reason == "note path changed before file publish: 42"
    # The file was written at the old path the DB no longer owns -> orphaned.
    assert plan.result.written_file_orphaned is True


def test_plan_written_note_materialization_publish_records_stale_written_file() -> None:
    request = materialization_request()

    plan = plan_written_note_materialization_publish(
        request=request,
        prepared_write=prepared_write(request),
        written_file=written_file(),
        current_note_content=note_content_state(
            db_version=5,
            db_checksum="newer-db-checksum",
        ),
        current_file_path="notes/a.md",
    )

    assert plan.action is NoteMaterializationPublishAction.stale_db_version
    assert plan.should_update_entity is False
    update = plan.require_note_content_update()
    assert update.file_write_status == "pending"
    assert update.file_version == 4
    assert update.file_checksum == "new-file-sum"
    assert plan.result == RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.stale,
        reason="file written but newer accepted note remains pending: 42",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
    )


def test_plan_written_note_materialization_publish_records_current_written_file() -> None:
    request = materialization_request()

    plan = plan_written_note_materialization_publish(
        request=request,
        prepared_write=prepared_write(request),
        written_file=written_file(),
        current_note_content=note_content_state(),
        current_file_path="notes/a.md",
    )

    assert plan.action is NoteMaterializationPublishAction.current
    assert plan.should_update_entity is True
    update = plan.require_note_content_update()
    assert update.file_write_status == "synced"
    assert update.file_version == 4
    assert update.file_checksum == "new-file-sum"
    assert plan.result == RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.written,
        reason="note file written: notes/a.md",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
    )


@pytest.mark.asyncio
async def test_run_note_materialization_writes_publishes_and_cleans_previous_file() -> None:
    request = materialization_request()
    prepared = prepared_write(request)
    written = written_file()
    result = RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.written,
        reason="note file materialized: notes/a.md",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
    )
    cleanup = FakeCleanupEnqueuer()

    actual = await run_note_materialization(
        request,
        preflight=FakePreflight(NoteMaterializationPreflightResult.prepared(prepared)),
        writer=FakeWriter(written),
        publisher=FakePublisher(result),
        status_publisher=FakeStatusPublisher(),
        cleanup_enqueuer=cleanup,
    )

    assert actual == result
    assert cleanup.requests == [
        RuntimeNoteFileDeleteJobRequest(
            project_id=request.project_id,
            entity_id=request.entity_id,
            file_path="notes/old.md",
            file_checksum="old-cleanup-sum",
            # The write destination rides along so a local adapter can skip the
            # delete when a case-only rename aliases old and new (P0 guard).
            live_file_path="notes/a.md",
        )
    ]


class FailingCleanupEnqueuer:
    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None:
        raise RuntimeError("queue unavailable")


@pytest.mark.asyncio
async def test_run_note_materialization_reports_cleanup_enqueue_failure_without_raising() -> None:
    """Regression: the S3 write and synced DB state are durable before cleanup is
    enqueued, so a transient queue error must not fail the job — it is surfaced on
    the result instead."""
    request = materialization_request()
    prepared = prepared_write(request)
    written = written_file()
    result = RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.written,
        reason="note file materialized: notes/a.md",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
    )

    actual = await run_note_materialization(
        request,
        preflight=FakePreflight(NoteMaterializationPreflightResult.prepared(prepared)),
        writer=FakeWriter(written),
        publisher=FakePublisher(result),
        status_publisher=FakeStatusPublisher(),
        cleanup_enqueuer=FailingCleanupEnqueuer(),
    )

    assert actual.status == RuntimeNoteMaterializationStatus.written
    assert actual.cleanup_enqueue_failed is True


@pytest.mark.asyncio
async def test_run_note_materialization_terminal_result_can_enqueue_cleanup() -> None:
    request = materialization_request()
    terminal_result = RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.missing,
        reason="note state no longer exists: 42",
    )
    cleanup = RuntimePendingNoteFileDelete(
        project_id=request.project_id,
        entity_id=request.entity_id,
        file_path="notes/old.md",
        file_checksum="old-cleanup-sum",
    )
    cleanup_enqueuer = FakeCleanupEnqueuer()

    actual = await run_note_materialization(
        request,
        preflight=FakePreflight(
            NoteMaterializationPreflightResult.terminal(
                terminal_result,
                cleanup_file=cleanup,
            )
        ),
        writer=FakeWriter(),
        publisher=FakePublisher(terminal_result),
        status_publisher=FakeStatusPublisher(),
        cleanup_enqueuer=cleanup_enqueuer,
    )

    assert actual == terminal_result
    assert cleanup_enqueuer.requests == [
        RuntimeNoteFileDeleteJobRequest(
            project_id=request.project_id,
            entity_id=request.entity_id,
            file_path="notes/old.md",
            file_checksum="old-cleanup-sum",
        )
    ]


@pytest.mark.asyncio
async def test_run_note_materialization_cleans_orphaned_file_on_stale_path() -> None:
    """A file written but no longer DB-owned (note moved before publish) is cleaned
    up so the watcher/index won't re-import it as a duplicate note."""
    request = materialization_request()
    prepared = prepared_write(request)
    result = RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.stale,
        reason="note path changed before file publish: 42",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
        written_file_orphaned=True,
    )
    cleanup = FakeCleanupEnqueuer()

    actual = await run_note_materialization(
        request,
        preflight=FakePreflight(NoteMaterializationPreflightResult.prepared(prepared)),
        writer=FakeWriter(written_file()),
        publisher=FakePublisher(result),
        status_publisher=FakeStatusPublisher(),
        cleanup_enqueuer=cleanup,
    )

    assert actual == result
    # The just-written file (not the prepared "old" file) is cleaned up.
    assert cleanup.requests == [
        RuntimeNoteFileDeleteJobRequest(
            project_id=request.project_id,
            entity_id=request.entity_id,
            file_path="notes/a.md",
            file_checksum="new-file-sum",
        )
    ]


@pytest.mark.asyncio
async def test_run_note_materialization_cleans_orphaned_file_on_missing_note() -> None:
    """Same cleanup when the note disappeared after the file write."""
    request = materialization_request()
    prepared = prepared_write(request)
    result = RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.missing,
        reason="note state disappeared after file write: 42",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
        written_file_orphaned=True,
    )
    cleanup = FakeCleanupEnqueuer()

    await run_note_materialization(
        request,
        preflight=FakePreflight(NoteMaterializationPreflightResult.prepared(prepared)),
        writer=FakeWriter(written_file()),
        publisher=FakePublisher(result),
        status_publisher=FakeStatusPublisher(),
        cleanup_enqueuer=cleanup,
    )

    assert cleanup.requests == [
        RuntimeNoteFileDeleteJobRequest(
            project_id=request.project_id,
            entity_id=request.entity_id,
            file_path="notes/a.md",
            file_checksum="new-file-sum",
        )
    ]


@pytest.mark.asyncio
async def test_run_note_materialization_keeps_file_on_stale_db_version() -> None:
    """A stale-db-version result must NOT clean up: the same path is re-materialized
    by the newer pending accepted version."""
    request = materialization_request()
    prepared = prepared_write(request)
    result = RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.stale,
        reason="file written but newer accepted note remains pending: 42",
        file_path="notes/a.md",
        file_checksum="new-file-sum",
        written_file_orphaned=False,
    )
    cleanup = FakeCleanupEnqueuer()

    await run_note_materialization(
        request,
        preflight=FakePreflight(NoteMaterializationPreflightResult.prepared(prepared)),
        writer=FakeWriter(written_file()),
        publisher=FakePublisher(result),
        status_publisher=FakeStatusPublisher(),
        cleanup_enqueuer=cleanup,
    )

    assert cleanup.requests == []


@pytest.mark.asyncio
async def test_run_note_materialization_records_conflict_status_and_returns_conflict() -> None:
    request = materialization_request()
    prepared = prepared_write(request)
    conflict = RuntimeFileConflictError(
        RuntimeFileConflict(
            file_path="notes/a.md",
            expected_checksum="old-file-sum",
            actual_checksum="external-sum",
        )
    )
    status_publisher = FakeStatusPublisher()

    actual = await run_note_materialization(
        request,
        preflight=FakePreflight(NoteMaterializationPreflightResult.prepared(prepared)),
        writer=FakeWriter(error=conflict),
        publisher=FakePublisher(
            RuntimeNoteMaterializationResult(
                entity_id=42,
                status=RuntimeNoteMaterializationStatus.written,
                reason="should not be used",
            )
        ),
        status_publisher=status_publisher,
        cleanup_enqueuer=FakeCleanupEnqueuer(),
    )

    assert actual == RuntimeNoteMaterializationResult(
        entity_id=42,
        status=RuntimeNoteMaterializationStatus.conflict,
        reason=str(conflict),
        file_path="notes/a.md",
        file_checksum="external-sum",
    )
    assert status_publisher.calls == [
        (
            request,
            NoteMaterializationStatusPublication(
                file_write_status="external_change_detected",
                attempted_at=prepared.attempted_at,
                actual_file_checksum="external-sum",
                error_message=str(conflict),
            ),
        )
    ]


@pytest.mark.asyncio
async def test_run_note_materialization_records_file_operation_failure_then_reraises() -> None:
    request = materialization_request()
    prepared = prepared_write(request)
    status_publisher = FakeStatusPublisher()

    with pytest.raises(FileOperationError):
        await run_note_materialization(
            request,
            preflight=FakePreflight(NoteMaterializationPreflightResult.prepared(prepared)),
            writer=FakeWriter(error=FileOperationError("storage unavailable")),
            publisher=FakePublisher(
                RuntimeNoteMaterializationResult(
                    entity_id=42,
                    status=RuntimeNoteMaterializationStatus.written,
                    reason="should not be used",
                )
            ),
            status_publisher=status_publisher,
            cleanup_enqueuer=FakeCleanupEnqueuer(),
        )

    assert status_publisher.calls == [
        (
            request,
            NoteMaterializationStatusPublication(
                file_write_status="failed",
                attempted_at=prepared.attempted_at,
                error_message="storage unavailable",
            ),
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        FileError("checksum failed"),
        OSError("stat failed"),
    ],
)
async def test_run_note_materialization_marks_other_storage_errors_failed(error) -> None:
    """Storage errors that are not RuntimeFileConflictError/FileOperationError
    (FileError from atomic write/checksum, OSError from the post-write stat) must
    still flip the note out of the 'writing' preflight state, not leave it stuck."""
    request = materialization_request()
    prepared = prepared_write(request)
    status_publisher = FakeStatusPublisher()

    with pytest.raises(type(error)):
        await run_note_materialization(
            request,
            preflight=FakePreflight(NoteMaterializationPreflightResult.prepared(prepared)),
            writer=FakeWriter(error=error),
            publisher=FakePublisher(
                RuntimeNoteMaterializationResult(
                    entity_id=42,
                    status=RuntimeNoteMaterializationStatus.written,
                    reason="should not be used",
                )
            ),
            status_publisher=status_publisher,
            cleanup_enqueuer=FakeCleanupEnqueuer(),
        )

    assert status_publisher.calls == [
        (
            request,
            NoteMaterializationStatusPublication(
                file_write_status="failed",
                attempted_at=prepared.attempted_at,
                error_message=str(error),
            ),
        )
    ]
