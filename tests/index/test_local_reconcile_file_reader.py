"""Tests for the local filesystem note_content reconcile file reader."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from basic_memory.index.local_dependencies import (
    FileServiceNoteContentReconcileFileReader,
    FileServiceReconcileFile,
)
from basic_memory.services.exceptions import FileOperationError
from basic_memory.services.file_service import FileService


@pytest.mark.asyncio
async def test_reader_returns_current_bytes_and_mtime(file_service: FileService) -> None:
    """The reader returns the current file bytes and modification time."""
    path = "note.md"
    # write_bytes: text mode would translate \n to \r\n on Windows, breaking the
    # byte-level assertion below
    (Path(file_service.base_path) / path).write_bytes(b"# Fresh\n")

    reader = FileServiceNoteContentReconcileFileReader(file_service=file_service)
    result = await reader.get_file(path)

    assert result.content == b"# Fresh\n"
    assert isinstance(result.last_modified, datetime)


@pytest.mark.asyncio
async def test_reader_returns_none_content_when_file_missing(file_service: FileService) -> None:
    """A file removed between scan and reconcile yields None content, not an error."""
    reader = FileServiceNoteContentReconcileFileReader(file_service=file_service)

    result = await reader.get_file("gone.md")

    assert result == FileServiceReconcileFile(content=None, last_modified=None)


@pytest.mark.asyncio
async def test_reader_reraises_non_missing_file_errors() -> None:
    """Read failures other than a missing file must propagate."""

    async def read_file_bytes(path: str) -> bytes:
        raise FileOperationError("boom") from ValueError("disk error")

    file_service = SimpleNamespace(read_file_bytes=read_file_bytes)
    reader = FileServiceNoteContentReconcileFileReader(file_service=cast(Any, file_service))

    with pytest.raises(FileOperationError):
        await reader.get_file("broken.md")
