"""Tests for portable note-file cleanup orchestration."""

import pytest

from basic_memory.indexing.note_file_delete_runner import run_note_file_delete
from basic_memory.runtime.cleanup import (
    RuntimeDeleteStatus,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
)
from basic_memory.services.exceptions import FileOperationError


class FakeNoteFileStorage:
    def __init__(self, checksum: str | None = "file-sum") -> None:
        self.checksum = checksum
        # Checksum seen by the guarded delete; defaults to the freshness-read value. Set it apart
        # from `checksum` to simulate a TOCTOU: matches at plan time, differs at delete time.
        self.checksum_at_delete: str | None = checksum
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

    async def delete_file_if_unchanged(self, path: str, *, expected_checksum: str) -> bool:
        self.delete_calls.append(path)
        if self.delete_error is not None:
            raise self.delete_error
        if self.checksum_at_delete != expected_checksum:
            return False
        return True


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
async def test_run_note_file_delete_skips_when_object_changes_before_delete() -> None:
    """TOCTOU guard: an object matching at read time but replaced before the delete is not removed.

    The guarded delete sees a different checksum and deletes nothing; the runner reports `changed`
    instead of claiming it deleted the replacement (basic-memory-cloud#1618).
    """
    storage = FakeNoteFileStorage(checksum="file-sum")
    # A writer replaces the object between the freshness read and the delete.
    storage.checksum_at_delete = "replacement-sum"

    result = await run_note_file_delete(delete_request(), storage=storage)

    assert result.status == RuntimeDeleteStatus.skipped
    assert result.reason == "file changed before delete: notes/a.md"
    # The guarded delete was attempted (and declined) — not skipped at plan time.
    assert storage.delete_calls == ["notes/a.md"]


@pytest.mark.asyncio
async def test_run_note_file_delete_propagates_delete_failures() -> None:
    storage = FakeNoteFileStorage(checksum="file-sum")
    storage.delete_error = FileOperationError("delete failed")

    with pytest.raises(FileOperationError, match="delete failed"):
        await run_note_file_delete(delete_request(), storage=storage)


class FakeVacateClearer:
    def __init__(self) -> None:
        self.cleared: list[tuple[int, str, str | None]] = []

    async def clear_move_vacate(
        self, *, project_id: int, file_path: str, file_checksum: str | None
    ) -> None:
        self.cleared.append((project_id, file_path, file_checksum))


@pytest.mark.asyncio
async def test_run_note_file_delete_clears_vacate_marker_on_delete() -> None:
    """When the source object is actually deleted, the move-vacate marker is cleared (#1601)."""
    storage = FakeNoteFileStorage(checksum="file-sum")
    clearer = FakeVacateClearer()

    result = await run_note_file_delete(delete_request(), storage=storage, vacate_clearer=clearer)

    assert result.status == RuntimeDeleteStatus.deleted
    assert clearer.cleared == [(101, "notes/a.md", "file-sum")]


@pytest.mark.asyncio
async def test_run_note_file_delete_clears_marker_after_source_replacement() -> None:
    """A guarded replacement proves the moved source object no longer needs suppression."""
    storage = FakeNoteFileStorage(checksum="changed-on-disk")  # != request checksum -> skip
    clearer = FakeVacateClearer()

    result = await run_note_file_delete(delete_request(), storage=storage, vacate_clearer=clearer)

    assert result.status == RuntimeDeleteStatus.skipped
    assert storage.delete_calls == []
    assert clearer.cleared == [(101, "notes/a.md", "file-sum")]


@pytest.mark.asyncio
async def test_run_note_file_delete_clears_marker_when_source_already_absent() -> None:
    """If the source is already gone (missing), still clear the marker (#1601 P2)."""
    storage = FakeNoteFileStorage(checksum=None)  # object already absent -> missing
    clearer = FakeVacateClearer()

    result = await run_note_file_delete(delete_request(), storage=storage, vacate_clearer=clearer)

    assert result.status == RuntimeDeleteStatus.missing
    assert clearer.cleared == [(101, "notes/a.md", "file-sum")]
