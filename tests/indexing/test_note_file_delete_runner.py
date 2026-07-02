"""Tests for portable note-file cleanup orchestration."""

import pytest

from basic_memory.indexing.note_file_delete_runner import run_note_file_delete
from basic_memory.runtime import (
    RuntimeDeleteStatus,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
)
from basic_memory.services.exceptions import FileOperationError


class FakeNoteFileStorage:
    def __init__(self, checksum: str | None = "file-sum") -> None:
        self.checksum = checksum
        self.exists_calls: list[str] = []
        self.compute_checksum_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.delete_error: FileOperationError | None = None

    async def exists(self, path: str) -> bool:
        self.exists_calls.append(path)
        return self.checksum is not None

    async def compute_checksum(self, path: str) -> str:
        self.compute_checksum_calls.append(path)
        if self.checksum is None:
            raise AssertionError("compute_checksum should not be called for absent files")
        return self.checksum

    async def delete_file(self, path: str) -> None:
        self.delete_calls.append(path)
        if self.delete_error is not None:
            raise self.delete_error


def delete_request(file_checksum: str | None = "file-sum") -> RuntimeNoteFileDeleteJobRequest:
    return RuntimeNoteFileDeleteJobRequest(
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum=file_checksum,
    )


@pytest.mark.asyncio
async def test_run_note_file_delete_deletes_matching_object() -> None:
    storage = FakeNoteFileStorage(checksum="file-sum")

    result = await run_note_file_delete(delete_request(), storage=storage)

    assert result == RuntimeFileDeleteResult(
        entity_id=42,
        file_path="notes/a.md",
        status=RuntimeDeleteStatus.deleted,
        reason="file deleted: notes/a.md",
    )
    assert storage.exists_calls == ["notes/a.md"]
    assert storage.compute_checksum_calls == ["notes/a.md"]
    assert storage.delete_calls == ["notes/a.md"]


@pytest.mark.asyncio
async def test_run_note_file_delete_treats_missing_object_as_done() -> None:
    storage = FakeNoteFileStorage(checksum=None)

    result = await run_note_file_delete(delete_request(), storage=storage)

    assert result.status == RuntimeDeleteStatus.missing
    assert result.reason == "file already absent: notes/a.md"
    assert storage.delete_calls == []


@pytest.mark.asyncio
async def test_run_note_file_delete_skips_without_accepted_checksum() -> None:
    storage = FakeNoteFileStorage(checksum="file-sum")

    result = await run_note_file_delete(delete_request(file_checksum=None), storage=storage)

    assert result.status == RuntimeDeleteStatus.skipped
    assert result.reason == "no accepted file checksum for notes/a.md"
    assert storage.exists_calls == []
    assert storage.compute_checksum_calls == []
    assert storage.delete_calls == []


@pytest.mark.asyncio
async def test_run_note_file_delete_skips_changed_object() -> None:
    storage = FakeNoteFileStorage(checksum="new-file-sum")

    result = await run_note_file_delete(delete_request(), storage=storage)

    assert result.status == RuntimeDeleteStatus.skipped
    assert result.reason == "file changed before delete: notes/a.md"
    assert storage.delete_calls == []


@pytest.mark.asyncio
async def test_run_note_file_delete_propagates_delete_failures() -> None:
    storage = FakeNoteFileStorage(checksum="file-sum")
    storage.delete_error = FileOperationError("delete failed")

    with pytest.raises(FileOperationError, match="delete failed"):
        await run_note_file_delete(delete_request(), storage=storage)
