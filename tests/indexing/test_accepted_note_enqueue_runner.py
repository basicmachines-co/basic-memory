"""Tests for portable accepted-note follow-up enqueue orchestration."""

from collections.abc import Mapping
from uuid import UUID

import pytest

from basic_memory.indexing.accepted_note_enqueue_runner import (
    AcceptedNoteEnqueueResult,
    enqueue_accepted_note_file_delete,
    enqueue_accepted_note_materialization,
    enqueue_accepted_note_write_jobs,
)
from basic_memory.runtime import (
    RuntimeAcceptedNoteChange,
    RuntimeNoteFileDeleteJobRequest,
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


def tenant_id() -> UUID:
    return UUID("11111111-1111-1111-1111-111111111111")


def payload_as_dict(payload: Mapping[str, object]) -> dict[str, object]:
    return dict(payload)


def accepted_materialization_change() -> RuntimeAcceptedNoteChange[Mapping[str, object]]:
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


def accepted_delete_change() -> RuntimeAcceptedNoteChange[Mapping[str, object]]:
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
        tenant_id=tenant_id(),
        payload_serializer=payload_as_dict,
        materialization_enqueuer=materialization_enqueuer,
        failure_marker=FakeMaterializationFailureMarker(),
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=202,
        payload={"file_write_status": "pending", "last_materialization_error": None},
    )
    assert materialization_enqueuer.requests == [
        RuntimeNoteMaterializationJobRequest(
            tenant_id=tenant_id(),
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
async def test_enqueue_accepted_note_materialization_marks_failed_status() -> None:
    failure_marker = FakeMaterializationFailureMarker()

    result = await enqueue_accepted_note_materialization(
        accepted_materialization_change(),
        tenant_id=tenant_id(),
        payload_serializer=payload_as_dict,
        materialization_enqueuer=FakeMaterializationEnqueuer(error=RuntimeError("pgq unavailable")),
        failure_marker=failure_marker,
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=202,
        payload={
            "file_write_status": "failed",
            "last_materialization_error": "pgq unavailable",
        },
    )
    assert failure_marker.calls == [(7, 42, "pgq unavailable")]


@pytest.mark.asyncio
async def test_enqueue_accepted_note_materialization_preserves_double_failure() -> None:
    with pytest.raises(ExceptionGroup) as exc_info:
        await enqueue_accepted_note_materialization(
            accepted_materialization_change(),
            tenant_id=tenant_id(),
            payload_serializer=payload_as_dict,
            materialization_enqueuer=FakeMaterializationEnqueuer(
                error=RuntimeError("pgq unavailable")
            ),
            failure_marker=FakeMaterializationFailureMarker(
                error=RuntimeError("cannot mark failed")
            ),
        )

    assert str(exc_info.value).startswith(
        "Failed to enqueue note materialization and mark the note as failed"
    )
    assert [str(error) for error in exc_info.value.exceptions] == [
        "pgq unavailable",
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
        tenant_id=tenant_id(),
        payload_serializer=payload_as_dict,
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
        tenant_id=tenant_id(),
        payload_serializer=payload_as_dict,
        file_delete_enqueuer=file_delete_enqueuer,
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=200,
        payload={"deleted": True, "file_delete_status": "pending"},
    )
    assert file_delete_enqueuer.requests == [
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=tenant_id(),
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
        tenant_id=tenant_id(),
        payload_serializer=payload_as_dict,
        file_delete_enqueuer=FakeFileDeleteEnqueuer(error=RuntimeError("pgq unavailable")),
    )

    assert result == AcceptedNoteEnqueueResult(
        status_code=200,
        payload={
            "deleted": True,
            "file_delete_status": "failed",
            "error": "pgq unavailable",
        },
    )


@pytest.mark.asyncio
async def test_enqueue_accepted_note_materialization_requires_materialization() -> None:
    with pytest.raises(RuntimeError, match="does not contain a materialization"):
        await enqueue_accepted_note_materialization(
            accepted_delete_change(),
            tenant_id=tenant_id(),
            payload_serializer=payload_as_dict,
            materialization_enqueuer=FakeMaterializationEnqueuer(),
            failure_marker=FakeMaterializationFailureMarker(),
        )


@pytest.mark.asyncio
async def test_enqueue_accepted_note_file_delete_requires_file_delete() -> None:
    with pytest.raises(RuntimeError, match="does not contain a file delete"):
        await enqueue_accepted_note_file_delete(
            accepted_materialization_change(),
            tenant_id=tenant_id(),
            payload_serializer=payload_as_dict,
            file_delete_enqueuer=FakeFileDeleteEnqueuer(),
        )
