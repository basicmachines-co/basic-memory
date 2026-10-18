"""Portable adapters for loaded files entering the indexer."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from basic_memory.indexing.models import IndexInputFile


class LoadedIndexFile(Protocol):
    """Storage-neutral file payload with content already loaded."""

    @property
    def size(self) -> int:
        """Loaded file size in bytes."""

    @property
    def checksum(self) -> str | None:
        """Current storage checksum, when available."""

    @property
    def last_modified(self) -> datetime | None:
        """Current storage last-modified timestamp, when available."""

    @property
    def created_at(self) -> datetime | None:
        """Current storage creation timestamp, when available.

        Used as the parser fallback for notes that do not declare frontmatter
        `created`. Callers that cannot provide a creation time may omit the
        attribute or return None; adaptation then leaves IndexInputFile.created_at
        as None and the parser falls back to now (#1772).
        """

    @property
    def content(self) -> bytes | None:
        """Loaded file content, when available."""


class IndexContentTypeProvider(Protocol):
    """Capability that determines the content type for an index path."""

    def content_type(self, path: str) -> str | None:
        """Return the canonical content type for the file path."""


def _loaded_created_at(file_info: LoadedIndexFile) -> datetime | None:
    """Read creation time from a loaded payload without requiring every adapter to declare it."""
    created_at = getattr(file_info, "created_at", None)
    return created_at if isinstance(created_at, datetime) else None


def build_index_input_files(
    files: Mapping[str, LoadedIndexFile],
    *,
    content_type_provider: IndexContentTypeProvider,
) -> dict[str, IndexInputFile]:
    """Convert loaded storage payloads into core batch-indexer inputs."""
    return {
        path: IndexInputFile(
            path=path,
            size=file_info.size,
            checksum=file_info.checksum,
            content_type=content_type_provider.content_type(path),
            last_modified=file_info.last_modified,
            # Preserve storage creation time when the loader supplied it. Hardcoding
            # None forced the markdown parser's ctime fallback to datetime.now() on
            # every full reindex for notes without frontmatter `created` (#1772).
            created_at=_loaded_created_at(file_info),
            content=file_info.content,
        )
        for path, file_info in files.items()
    }
