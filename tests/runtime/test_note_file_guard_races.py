"""Race regressions for portable note-file concurrency guards."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.file_utils import FileError
from basic_memory.index.note_content_materialization import LocalNoteContentStorage
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


async def test_direct_checksum_preserves_file_service_error_contract(tmp_path: Path) -> None:
    """Direct callers still receive FileError when the checksum source is absent."""
    file_service = FileService(tmp_path)

    with pytest.raises(FileError):
        await file_service.compute_checksum("notes/disappeared.md")
