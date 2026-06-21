"""Tests for portable observed index-file adapters."""

from dataclasses import FrozenInstanceError, dataclass

import pytest

from basic_memory.runtime import (
    RuntimeObservedIndexFile,
    runtime_observed_index_files_from_metadata_map,
)


@dataclass(frozen=True, slots=True)
class StorageMetadata:
    checksum: str | None
    size: int | None


def test_runtime_observed_index_files_from_metadata_map_uses_sorted_mapping_paths() -> None:
    """Observed project-index targets are stable runtime values before queue serialization."""
    metadata_by_path = {
        "assets/image.png": StorageMetadata(checksum="etag-image", size=30),
        "notes/longform.markdown": StorageMetadata(checksum="etag-longform", size=40),
        "notes/b.md": StorageMetadata(checksum="etag-b", size=20),
        "notes/a.md": StorageMetadata(checksum=None, size=None),
        "notes/todo.txt": StorageMetadata(checksum="etag-todo", size=10),
    }

    observed_files = runtime_observed_index_files_from_metadata_map(metadata_by_path)

    assert observed_files == (
        RuntimeObservedIndexFile(path="notes/a.md", checksum=None, size=None),
        RuntimeObservedIndexFile(path="notes/b.md", checksum="etag-b", size=20),
        RuntimeObservedIndexFile(path="notes/longform.markdown", checksum="etag-longform", size=40),
    )
    with pytest.raises(FrozenInstanceError):
        setattr(observed_files[0], "path", "notes/changed.md")
