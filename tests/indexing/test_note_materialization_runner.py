"""Tests for portable note materialization orchestration."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from basic_memory.indexing.note_materialization_runner import (
    NoteMaterializationPreflightResult,
    NoteMaterializationStatusPublication,
    plan_note_materialization_preflight,
    run_note_materialization,
)
from basic_memory.runtime import (
    RuntimeFileConflict,
    RuntimeFileConflictError,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteFileDelete,
    RuntimePreparedNoteWrite,
    RuntimeWrittenFileState,
    plan_prepared_note_write,
)
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
        error: RuntimeFileConflictError | FileOperationError | None = None,
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


def materialization_request() -> RuntimeNoteMaterializationJobRequest:
    return RuntimeNoteMaterializationJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
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
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            entity_id=request.entity_id,
            file_path="notes/old.md",
            file_checksum="old-cleanup-sum",
        )
    ]


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
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            entity_id=request.entity_id,
            file_path="notes/old.md",
            file_checksum="old-cleanup-sum",
        )
    ]


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
