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
        """Current storage creation timestamp, or None when storage records none.

        The parser's creation time for a new note that declares no frontmatter
        `created`. Object storage keeps no creation time, so its loaders answer
        None and the parser falls back to now (#1772).
        """

    @property
    def content(self) -> bytes | None:
        """Loaded file content, when available."""


class IndexContentTypeProvider(Protocol):
    """Capability that determines the content type for an index path."""

    def content_type(self, path: str) -> str | None:
        """Return the canonical content type for the file path."""


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
            # Hardcoding None here made the parser stamp every new note without a
            # frontmatter `created` with the time of the index run (#1772).
            created_at=file_info.created_at,
            content=file_info.content,
        )
        for path, file_info in files.items()
    }
