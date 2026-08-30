"""Race regressions for portable note-file concurrency guards."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

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
