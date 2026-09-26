"""Tests for portable note-file cleanup orchestration."""

import pytest

from basic_memory.indexing.note_file_delete_runner import run_note_file_delete
from basic_memory.runtime.cleanup import (
    RuntimeDeleteStatus,
    RuntimeFileDeleteResult,
    RuntimeGuardedFileDeleteOutcome,
    RuntimeNoteFileDeleteJobRequest,
)
from basic_memory.services.exceptions import FileOperationError


class FakeNoteFileStorage:
    """Storage that reports a fixed guarded-delete outcome and records what it was asked."""

    def __init__(
        self,
        outcome: RuntimeGuardedFileDeleteOutcome = RuntimeGuardedFileDeleteOutcome.deleted,
    ) -> None:
        self.outcome = outcome
        self.delete_calls: list[tuple[str, str]] = []
        self.delete_error: FileOperationError | None = None

    async def delete_file_if_matches(
        self, path: str, *, expected_checksum: str
    ) -> RuntimeGuardedFileDeleteOutcome:
        self.delete_calls.append((path, expected_checksum))
        if self.delete_error is not None:
            raise self.delete_error
        return self.outcome


def delete_request(file_checksum: str | None = "file-sum") -> RuntimeNoteFileDeleteJobRequest:
    return RuntimeNoteFileDeleteJobRequest(
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum=file_checksum,
    )


@pytest.mark.asyncio
async def test_run_note_file_delete_deletes_matching_object() -> None:
    storage = FakeNoteFileStorage(RuntimeGuardedFileDeleteOutcome.deleted)

    result = await run_note_file_delete(delete_request(), storage=storage)

    assert result == RuntimeFileDeleteResult(
        entity_id=42,
        file_path="notes/a.md",
        status=RuntimeDeleteStatus.deleted,
        reason="file deleted: notes/a.md",
    )
    assert storage.delete_calls == [("notes/a.md", "file-sum")]


@pytest.mark.asyncio
async def test_run_note_file_delete_hands_the_accepted_checksum_to_storage_unchanged() -> None:
    """The runner never compares checksums itself (basic-memory-cloud#2167).

    An entity row can record an object-store version tag (an S3 ETag) rather than a content
    checksum. A runner-side equality check against a content checksum rejected every such
    delete, so the accepted value must reach storage verbatim for storage to match.
    """
    storage = FakeNoteFileStorage(RuntimeGuardedFileDeleteOutcome.deleted)
    etag_checksum = "6e4674b373734364cdf6753d7e6334ec"

    result = await run_note_file_delete(delete_request(etag_checksum), storage=storage)

    assert result.status == RuntimeDeleteStatus.deleted
    assert storage.delete_calls == [("notes/a.md", etag_checksum)]


@pytest.mark.asyncio
async def test_run_note_file_delete_treats_missing_object_as_done() -> None:
    storage = FakeNoteFileStorage(RuntimeGuardedFileDeleteOutcome.missing)

    result = await run_note_file_delete(delete_request(), storage=storage)

    assert result.status == RuntimeDeleteStatus.missing
    assert result.reason == "file already absent: notes/a.md"


@pytest.mark.asyncio
async def test_run_note_file_delete_skips_without_accepted_checksum() -> None:
    storage = FakeNoteFileStorage()

    result = await run_note_file_delete(delete_request(file_checksum=None), storage=storage)

    assert result.status == RuntimeDeleteStatus.skipped
    assert result.reason == "no accepted file checksum for notes/a.md"
    assert storage.delete_calls == []


@pytest.mark.asyncio
async def test_run_note_file_delete_skips_changed_object() -> None:
    """A replaced object is reported as changed, never as deleted (basic-memory-cloud#1618)."""
    storage = FakeNoteFileStorage(RuntimeGuardedFileDeleteOutcome.changed)

    result = await run_note_file_delete(delete_request(), storage=storage)

    assert result.status == RuntimeDeleteStatus.skipped
    assert result.reason == "file changed before delete: notes/a.md"


@pytest.mark.asyncio
async def test_run_note_file_delete_propagates_delete_failures() -> None:
    storage = FakeNoteFileStorage()
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
    storage = FakeNoteFileStorage(RuntimeGuardedFileDeleteOutcome.deleted)
    clearer = FakeVacateClearer()

    result = await run_note_file_delete(delete_request(), storage=storage, vacate_clearer=clearer)

    assert result.status == RuntimeDeleteStatus.deleted
    assert clearer.cleared == [(101, "notes/a.md", "file-sum")]


@pytest.mark.asyncio
async def test_run_note_file_delete_clears_marker_after_source_replacement() -> None:
    """A guarded replacement proves the moved source object no longer needs suppression."""
    storage = FakeNoteFileStorage(RuntimeGuardedFileDeleteOutcome.changed)
    clearer = FakeVacateClearer()

    result = await run_note_file_delete(delete_request(), storage=storage, vacate_clearer=clearer)

    assert result.status == RuntimeDeleteStatus.skipped
    assert clearer.cleared == [(101, "notes/a.md", "file-sum")]


@pytest.mark.asyncio
async def test_run_note_file_delete_clears_marker_when_source_already_absent() -> None:
    """If the source is already gone (missing), still clear the marker (#1601 P2)."""
    storage = FakeNoteFileStorage(RuntimeGuardedFileDeleteOutcome.missing)
    clearer = FakeVacateClearer()

    result = await run_note_file_delete(delete_request(), storage=storage, vacate_clearer=clearer)

    assert result.status == RuntimeDeleteStatus.missing
    assert clearer.cleared == [(101, "notes/a.md", "file-sum")]
