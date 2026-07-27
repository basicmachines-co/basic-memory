"""Tests for local filesystem runtime adapters."""

from dataclasses import dataclass, field
from typing import cast

import pytest

from basic_memory.file_utils import FileError
from basic_memory.index.local_runtime import LocalStorageFileMetadataSource
from basic_memory.services import FileService
from basic_memory.services.exceptions import FileOperationError


@dataclass(slots=True)
class StubFileService:
    checksums: dict[str, str]
    failures: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def exists(self, file_path: str) -> bool:
        self.calls.append(("exists", file_path))
        return file_path in self.checksums or file_path in self.failures

    async def compute_checksum(self, file_path: str) -> str:
        self.calls.append(("checksum", file_path))
        failure = self.failures.get(file_path)
        if failure == "operation-missing":
            raise FileOperationError(f"file vanished: {file_path}") from FileNotFoundError(
                file_path
            )
        if failure == "checksum-missing":
            raise FileError(f"checksum target vanished: {file_path}") from FileNotFoundError(
                file_path
            )
        if failure == "permission":
            raise FileError(f"permission denied: {file_path}") from PermissionError(file_path)
        return self.checksums[file_path]


def metadata_source(file_service: StubFileService) -> LocalStorageFileMetadataSource:
    return LocalStorageFileMetadataSource(cast(FileService, file_service))


@pytest.mark.asyncio
async def test_local_storage_checksum_source_loads_current_checksum() -> None:
    file_service = StubFileService(checksums={"note.md": "checksum-current"})
    source = metadata_source(file_service)

    assert await source.load_current_file_checksum("note.md") == "checksum-current"
    assert await source.load_current_file_checksum("missing.md") is None
    assert file_service.calls == [
        ("exists", "note.md"),
        ("checksum", "note.md"),
        ("exists", "missing.md"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["operation-missing", "checksum-missing"])
async def test_local_storage_checksum_source_treats_file_disappearance_as_missing(
    failure: str,
) -> None:
    source = metadata_source(StubFileService(checksums={}, failures={"vanished.md": failure}))

    assert await source.load_current_file_checksum("vanished.md") is None


@pytest.mark.asyncio
async def test_local_storage_checksum_source_propagates_non_missing_failures() -> None:
    source = metadata_source(
        StubFileService(checksums={}, failures={"unreadable.md": "permission"})
    )

    with pytest.raises(FileError, match="permission denied"):
        await source.load_current_file_checksum("unreadable.md")
