"""Tests for portable directory-delete cleanup orchestration."""

from uuid import UUID

import pytest

from basic_memory.indexing.directory_delete_runner import (
    DirectoryDeleteAcceptedResult,
    enqueue_directory_file_delete_jobs,
)
from basic_memory.runtime import (
    RuntimeDirectoryFileSnapshot,
    RuntimeNoteFileDeleteJobRequest,
)


class FakeDirectoryFileDeleteEnqueuer:
    def __init__(self) -> None:
        self.requests: list[RuntimeNoteFileDeleteJobRequest] = []

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        self.requests.append(request)


def directory_snapshot(
    *,
    entity_id: int = 7,
    file_path: str = "notes/example.md",
    file_checksum: str | None = "note-sha",
) -> RuntimeDirectoryFileSnapshot:
    return RuntimeDirectoryFileSnapshot(
        entity_id=entity_id,
        file_path=file_path,
        file_checksum=file_checksum,
        last_modified_at=160.0,
        size=None,
    )


def test_directory_delete_result_serializes_empty_complete_shape() -> None:
    result = DirectoryDeleteAcceptedResult.complete()

    assert result.to_response_payload() == {
        "total_files": 0,
        "successful_deletes": 0,
        "failed_deletes": 0,
        "deleted_files": [],
        "errors": [],
        "file_delete_status": "complete",
    }


def test_directory_delete_result_serializes_pending_deleted_files() -> None:
    result = DirectoryDeleteAcceptedResult.pending(
        deleted_files=("notes/a.md", "notes/b.md"),
    )

    assert result.to_response_payload() == {
        "total_files": 2,
        "successful_deletes": 2,
        "failed_deletes": 0,
        "deleted_files": ["notes/a.md", "notes/b.md"],
        "errors": [],
        "file_delete_status": "pending",
    }


def test_directory_delete_result_serializes_failed_enqueue_error() -> None:
    result = DirectoryDeleteAcceptedResult.failed(
        deleted_files=("notes/a.md",),
        error="queue unavailable",
    )

    assert result.to_response_payload() == {
        "total_files": 1,
        "successful_deletes": 1,
        "failed_deletes": 0,
        "deleted_files": ["notes/a.md"],
        "errors": [],
        "file_delete_status": "failed",
        "error": "queue unavailable",
    }


@pytest.mark.asyncio
async def test_enqueue_directory_file_delete_jobs_maps_runtime_snapshots() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    enqueuer = FakeDirectoryFileDeleteEnqueuer()

    await enqueue_directory_file_delete_jobs(
        tenant_id=tenant_id,
        project_id=3,
        files=[
            directory_snapshot(entity_id=7, file_path="notes/example.md"),
            directory_snapshot(
                entity_id=8,
                file_path="notes/legacy.md",
                file_checksum=None,
            ),
        ],
        enqueuer=enqueuer,
    )

    assert enqueuer.requests == [
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=tenant_id,
            project_id=3,
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="note-sha",
        ),
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=tenant_id,
            project_id=3,
            entity_id=8,
            file_path="notes/legacy.md",
            file_checksum=None,
        ),
    ]
