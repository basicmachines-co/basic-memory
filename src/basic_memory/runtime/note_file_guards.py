"""Portable optimistic-concurrency guards for accepted note files."""

from dataclasses import dataclass
from typing import Protocol

from basic_memory.runtime.storage import RuntimeFileChecksum, RuntimeFilePath


class RuntimeFileChecksumReader(Protocol):
    """Capability for reading a runtime file checksum if an object exists."""

    async def exists(self, path: RuntimeFilePath) -> bool: ...

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum: ...


@dataclass(frozen=True, slots=True)
class RuntimeExpectedFileState:
    """The storage object state a guarded write expects to find."""

    file_path: RuntimeFilePath
    expected_checksum: RuntimeFileChecksum | None


@dataclass(frozen=True, slots=True)
class RuntimeFileConflict:
    """Storage object state that does not match a guarded write."""

    file_path: RuntimeFilePath
    expected_checksum: RuntimeFileChecksum | None
    actual_checksum: RuntimeFileChecksum

    @property
    def message(self) -> str:
        if self.expected_checksum is None:
            return (
                f"Refusing to overwrite unexpected file at {self.file_path}: "
                f"expected no existing object, found checksum {self.actual_checksum}"
            )
        return (
            f"Refusing to overwrite unexpected file at {self.file_path}: "
            f"expected checksum {self.expected_checksum}, found {self.actual_checksum}"
        )


class RuntimeFileConflictError(RuntimeError):
    """Raised when storage no longer matches the expected file state."""

    def __init__(self, conflict: RuntimeFileConflict) -> None:
        super().__init__(conflict.message)
        self.conflict = conflict
        self.file_path = conflict.file_path
        self.expected_checksum = conflict.expected_checksum
        self.actual_checksum = conflict.actual_checksum


async def read_runtime_file_checksum(
    reader: RuntimeFileChecksumReader,
    file_path: RuntimeFilePath,
) -> RuntimeFileChecksum | None:
    """Return the current runtime file checksum, or None when absent."""
    if not await reader.exists(file_path):
        return None
    return await reader.compute_checksum(file_path)


def runtime_file_conflict(
    actual_checksum: RuntimeFileChecksum | None,
    expected_checksum: RuntimeFileChecksum | None,
    file_path: RuntimeFilePath,
) -> RuntimeFileConflict | None:
    """Return a conflict when a present file does not match the expected checksum.

    An absent file never conflicts. A present file conflicts unless the caller
    expected exactly that checksum; a None expectation always conflicts with a
    present file.
    """
    if actual_checksum is None:
        return None
    if expected_checksum is None or actual_checksum != expected_checksum:
        return RuntimeFileConflict(
            file_path=file_path,
            expected_checksum=expected_checksum,
            actual_checksum=actual_checksum,
        )
    return None


async def assert_runtime_file_matches_expected(
    reader: RuntimeFileChecksumReader,
    expected: RuntimeExpectedFileState,
) -> None:
    """Raise when a guarded write would overwrite an unexpected runtime file."""
    actual_checksum = await read_runtime_file_checksum(reader, expected.file_path)
    conflict = runtime_file_conflict(
        actual_checksum,
        expected.expected_checksum,
        expected.file_path,
    )
    if conflict is not None:
        raise RuntimeFileConflictError(conflict)
