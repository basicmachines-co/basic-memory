"""Adapt observed storage metadata into runtime index-file targets."""

from collections.abc import Mapping
from typing import Protocol

from basic_memory.runtime.contracts import (
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeObservedIndexFile,
    runtime_file_path_is_markdown_note,
)


class RuntimeObservedIndexFileMetadataSource(Protocol):
    """Minimal storage metadata needed for project-index file fanout."""

    @property
    def checksum(self) -> RuntimeFileChecksum | None: ...

    @property
    def size(self) -> int | None: ...


def runtime_observed_index_file_from_metadata(
    path: RuntimeFilePath,
    metadata: RuntimeObservedIndexFileMetadataSource,
) -> RuntimeObservedIndexFile:
    """Map one storage metadata record into the runtime observed-file value."""
    return RuntimeObservedIndexFile(
        path=path,
        checksum=metadata.checksum,
        size=metadata.size,
    )


def runtime_observed_index_files_from_metadata_map(
    metadata_by_path: Mapping[RuntimeFilePath, RuntimeObservedIndexFileMetadataSource],
) -> tuple[RuntimeObservedIndexFile, ...]:
    """Return stable markdown-note targets from path-keyed storage metadata."""
    return tuple(
        runtime_observed_index_file_from_metadata(
            path,
            metadata_by_path[path],
        )
        for path in sorted(metadata_by_path)
        if runtime_file_path_is_markdown_note(path)
    )
