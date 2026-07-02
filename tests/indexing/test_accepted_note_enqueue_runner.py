"""Tests for portable accepted-note follow-up enqueue orchestration."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from basic_memory.indexing.accepted_note_enqueue_runner import (
    AcceptedNoteEnqueueResult,
    enqueue_accepted_note_file_delete,
    enqueue_accepted_note_materialization,
    enqueue_accepted_note_write_jobs,
)
from basic_memory.runtime.cleanup import RuntimeNoteFileDeleteJobRequest
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteResponse,
    RuntimeNoteContentResponsePayload,
    RuntimeNoteMaterializationJobRequest,
    RuntimePendingNoteFileDelete,
    RuntimePendingNoteMaterialization,
)


class FakeMaterializationEnqueuer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[RuntimeNoteMaterializationJobRequest] = []

    async def enqueue_note_materialization(
        self,
        request: RuntimeNoteMaterializationJobRequest,
    ) -> None:
        self.requests.append(request)
        if self.error is not None:
            raise self.error


class FakeMaterializationFailureMarker:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[int, int, str]] = []

    async def mark_note_materialization_failed(
        self,
        *,
        project_id: int,
        entity_id: int,
        error_message: str,
    ) -> None:
        self.calls.append((project_id, entity_id, error_message))
        if self.error is not None:
            raise self.error


class FakeFileDeleteEnqueuer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[RuntimeNoteFileDeleteJobRequest] = []

    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None:
        self.requests.append(request)
        if self.error is not None:
            raise self.error


def accepted_materialization_change() -> RuntimeAcceptedNoteChange[
    RuntimeNoteContentResponsePayload
]:
    return RuntimeAcceptedNoteChange(
        status_code=202,
        payload={"file_write_status": "pending", "last_materialization_error": None},
        materialization=RuntimePendingNoteMaterialization(
            project_id=7,
            entity_id=42,
            db_version=3,
            db_checksum="db-checksum",
            actor_user_profile_id=UUID("22222222-2222-2222-2222-222222222222"),
            actor_kind="mcp_client",
            actor_name="Claude Code",
            source="mcp",
        ),
    )


def accepted_delete_change() -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
    return RuntimeAcceptedNoteChange(
        status_code=200,
        payload={"deleted": True, "file_delete_status": "pending"},
        file_delete=RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/deleted.md",
            file_checksum="file-checksum",
        ),
    )


@pytest.mark.asyncio
async def test_enqueue_accepted_note_materialization_queues_runtime_request() -> None:
    materialization_enqueuer = FakeMaterializationEnqueuer()

    result = await enqueue_accepted_note_materialization(
        accepted_materialization_change(),
        materialization_enqueuer=materialization_enqueuer,
        failure_marker=FakeMaterializationFailureMarker(),
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=202,
        payload={"file_write_status": "pending", "last_materialization_error": None},
    )
    assert materialization_enqueuer.requests == [
        RuntimeNoteMaterializationJobRequest(
            project_id=7,
            entity_id=42,
            db_version=3,
            db_checksum="db-checksum",
            actor_user_profile_id=UUID("22222222-2222-2222-2222-222222222222"),
            actor_kind="mcp_client",
            actor_name="Claude Code",
            source="mcp",
        )
    ]


@pytest.mark.asyncio
async def test_enqueue_accepted_note_materialization_keeps_typed_payload_on_failure() -> None:
    response = RuntimeAcceptedNoteResponse(
        external_id="note-1",
        entity_id=42,
        title="Typed note",
        note_type="note",
        content_type="text/markdown",
        permalink="typed-note",
        file_path="notes/typed.md",
        markdown_content="# Typed\n",
        entity_metadata={"topic": "runtime"},
        created_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        created_by="creator",
        last_updated_by="editor",
        db_version=3,
        db_checksum="db-checksum",
        file_version=None,
        file_checksum=None,
        file_write_status="pending",
        last_source="api",
        file_updated_at=None,
        last_materialization_error=None,
    )
    failure_marker = FakeMaterializationFailureMarker()

    result = await enqueue_accepted_note_materialization(
        RuntimeAcceptedNoteChange(
            status_code=202,
            payload=response,
            materialization=RuntimePendingNoteMaterialization(
                project_id=7,
                entity_id=42,
                db_version=3,
                db_checksum="db-checksum",
            ),
        ),
        materialization_enqueuer=FakeMaterializationEnqueuer(
            error=RuntimeError("queue unavailable")
        ),
        failure_marker=failure_marker,
    )

    assert result.status_code == 202
    assert isinstance(result.payload, RuntimeAcceptedNoteResponse)
    assert result.payload.file_write_status == "failed"
    assert result.payload.last_materialization_error == "queue unavailable"
    assert result.payload.to_response_payload()["external_id"] == "note-1"
    assert failure_marker.calls == [(7, 42, "queue unavailable")]


@pytest.mark.asyncio
async def test_enqueue_accepted_note_materialization_marks_failed_status() -> None:
    failure_marker = FakeMaterializationFailureMarker()

    result = await enqueue_accepted_note_materialization(
        accepted_materialization_change(),
        materialization_enqueuer=FakeMaterializationEnqueuer(
            error=RuntimeError("queue unavailable")
        ),
        failure_marker=failure_marker,
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=202,
        payload={
            "file_write_status": "failed",
            "last_materialization_error": "queue unavailable",
        },
    )
    assert failure_marker.calls == [(7, 42, "queue unavailable")]


@pytest.mark.asyncio
async def test_enqueue_accepted_note_materialization_preserves_double_failure() -> None:
    with pytest.raises(ExceptionGroup) as exc_info:
        await enqueue_accepted_note_materialization(
            accepted_materialization_change(),
            materialization_enqueuer=FakeMaterializationEnqueuer(
                error=RuntimeError("queue unavailable")
            ),
            failure_marker=FakeMaterializationFailureMarker(
                error=RuntimeError("cannot mark failed")
            ),
        )

    assert str(exc_info.value).startswith(
        "Failed to enqueue note materialization and mark the note as failed"
    )
    assert [str(error) for error in exc_info.value.exceptions] == [
        "queue unavailable",
        "cannot mark failed",
    ]


@pytest.mark.asyncio
async def test_enqueue_accepted_note_write_jobs_embeds_cleanup_in_materialization() -> None:
    materialization_enqueuer = FakeMaterializationEnqueuer()
    file_delete_enqueuer = FakeFileDeleteEnqueuer()
    accepted = RuntimeAcceptedNoteChange(
        status_code=200,
        payload={"file_write_status": "pending"},
        materialization=RuntimePendingNoteMaterialization(
            project_id=7,
            entity_id=42,
            db_version=3,
            db_checksum="db-checksum",
            cleanup_after_write=RuntimePendingNoteFileDelete(
                project_id=7,
                entity_id=42,
                file_path="notes/old.md",
                file_checksum="old-file",
            ),
        ),
    )

    result = await enqueue_accepted_note_write_jobs(
        accepted,
        materialization_enqueuer=materialization_enqueuer,
        failure_marker=FakeMaterializationFailureMarker(),
        file_delete_enqueuer=file_delete_enqueuer,
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=200,
        payload={"file_write_status": "pending"},
    )
    assert len(materialization_enqueuer.requests) == 1
    queued_request = materialization_enqueuer.requests[0]
    assert queued_request.cleanup_file_path == "notes/old.md"
    assert queued_request.cleanup_file_checksum == "old-file"
    assert file_delete_enqueuer.requests == []


@pytest.mark.asyncio
async def test_enqueue_accepted_note_file_delete_queues_runtime_request() -> None:
    file_delete_enqueuer = FakeFileDeleteEnqueuer()

    result = await enqueue_accepted_note_file_delete(
        accepted_delete_change(),
        file_delete_enqueuer=file_delete_enqueuer,
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=200,
        payload={"deleted": True, "file_delete_status": "pending"},
    )
    assert file_delete_enqueuer.requests == [
        RuntimeNoteFileDeleteJobRequest(
            project_id=7,
            entity_id=42,
            file_path="notes/deleted.md",
            file_checksum="file-checksum",
        )
    ]


@pytest.mark.asyncio
async def test_enqueue_accepted_note_file_delete_marks_enqueue_failure() -> None:
    result = await enqueue_accepted_note_file_delete(
        accepted_delete_change(),
        file_delete_enqueuer=FakeFileDeleteEnqueuer(error=RuntimeError("queue unavailable")),
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=200,
        payload={
            "deleted": True,
            "file_delete_status": "failed",
            "error": "queue unavailable",
        },
    )


@pytest.mark.asyncio
async def test_enqueue_accepted_note_materialization_requires_materialization() -> None:
    with pytest.raises(RuntimeError, match="does not contain a materialization"):
        await enqueue_accepted_note_materialization(
            accepted_delete_change(),
            materialization_enqueuer=FakeMaterializationEnqueuer(),
            failure_marker=FakeMaterializationFailureMarker(),
        )


@pytest.mark.asyncio
async def test_enqueue_accepted_note_file_delete_requires_file_delete() -> None:
    with pytest.raises(RuntimeError, match="does not contain a file delete"):
        await enqueue_accepted_note_file_delete(
            accepted_materialization_change(),
            file_delete_enqueuer=FakeFileDeleteEnqueuer(),
        )
