"""Race regressions for portable note-file concurrency guards."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.file_utils import FileError
from basic_memory.index.local_notes import LocalNoteFileDeleteStorage
from basic_memory.index.note_content_materialization import LocalNoteContentStorage
from basic_memory.indexing.note_file_delete_runner import run_note_file_delete
from basic_memory.runtime.cleanup import RuntimeDeleteStatus, RuntimeNoteFileDeleteJobRequest
from basic_memory.runtime.note_file_guards import read_runtime_file_checksum
from basic_memory.services.file_service import FileService


async def test_checksum_read_treats_post_probe_deletion_as_absent(tmp_path: Path) -> None:
    """A disappearing object should not poison a durable materialization retry."""
    file_service = FileService(tmp_path)
    storage = LocalNoteContentStorage(file_service)

    # The file disappears after storage reports it present but before checksum I/O.
    with patch.object(file_service, "exists", AsyncMock(return_value=True)):
        checksum = await read_runtime_file_checksum(storage, "notes/disappeared.md")

    assert checksum is None


async def test_directory_delete_converges_when_file_disappears_before_delete(
    tmp_path: Path,
) -> None:
    """The final guarded checksum should treat a vanished target as a safe no-delete."""
    file_service = FileService(tmp_path)
    file_path = "notes/disappeared.md"
    await file_service.write_file(file_path, "# Disappearing note\n")
    accepted_checksum = await file_service.compute_checksum(file_path)
    original_compute_checksum = file_service.compute_checksum
    checksum_calls = 0

    async def delete_before_final_checksum(path: str) -> str:
        nonlocal checksum_calls
        checksum_calls += 1
        if checksum_calls == 2:
            (tmp_path / path).unlink()
        return await original_compute_checksum(path)

    with patch.object(file_service, "compute_checksum", side_effect=delete_before_final_checksum):
        result = await run_note_file_delete(
            RuntimeNoteFileDeleteJobRequest(
                project_id=101,
                entity_id=42,
                file_path=file_path,
                file_checksum=accepted_checksum,
            ),
            storage=LocalNoteFileDeleteStorage(file_service),
        )

    assert result.status == RuntimeDeleteStatus.skipped
    assert result.reason == f"file changed before delete: {file_path}"
    assert not (tmp_path / file_path).exists()


async def test_note_delete_converges_when_file_disappears_before_delete(
    tmp_path: Path,
) -> None:
    """Ordinary note cleanup should share the safe final-checksum outcome."""
    file_service = FileService(tmp_path)
    file_path = "notes/disappeared.md"
    await file_service.write_file(file_path, "# Disappearing note\n")
    accepted_checksum = await file_service.compute_checksum(file_path)
    original_compute_checksum = file_service.compute_checksum
    checksum_calls = 0

    async def delete_before_final_checksum(path: str) -> str:
        nonlocal checksum_calls
        checksum_calls += 1
        if checksum_calls == 2:
            (tmp_path / path).unlink()
        return await original_compute_checksum(path)

    with patch.object(file_service, "compute_checksum", side_effect=delete_before_final_checksum):
        result = await run_note_file_delete(
            RuntimeNoteFileDeleteJobRequest(
                project_id=101,
                entity_id=42,
                file_path=file_path,
                file_checksum=accepted_checksum,
            ),
            storage=LocalNoteContentStorage(file_service),
        )

    assert result.status == RuntimeDeleteStatus.skipped
    assert result.reason == f"file changed before delete: {file_path}"
    assert not (tmp_path / file_path).exists()


async def test_direct_checksum_preserves_file_service_error_contract(tmp_path: Path) -> None:
    """Direct callers still receive FileError when the checksum source is absent."""
    file_service = FileService(tmp_path)

    with pytest.raises(FileError):
        await file_service.compute_checksum("notes/disappeared.md")


async def test_directory_delete_checksum_treats_post_probe_deletion_as_absent(
    tmp_path: Path,
) -> None:
    """Directory cleanup should converge when its target disappears before checksum I/O."""
    file_service = FileService(tmp_path)
    storage = LocalNoteFileDeleteStorage(file_service)

    with patch.object(file_service, "exists", AsyncMock(return_value=True)):
        checksum = await read_runtime_file_checksum(storage, "notes/disappeared.md")

    assert checksum is None
