"""Tests for portable index input-file adaptation."""

from dataclasses import dataclass
from datetime import datetime, timezone

from basic_memory.indexing.input_file_adaptation import build_index_input_files
from basic_memory.indexing.models import IndexInputFile


@dataclass(frozen=True, slots=True)
class LoadedFile:
    """Storage-neutral loaded file payload for tests."""

    size: int
    checksum: str | None
    last_modified: datetime | None
    content: bytes | None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LoadedFileWithoutCreatedAt:
    """Legacy loader shape that never advertised creation time."""

    size: int
    checksum: str | None
    last_modified: datetime | None
    content: bytes | None


class PathContentTypeProvider:
    """Returns deterministic content types from paths."""

    def content_type(self, path: str) -> str:
        if path.endswith(".md"):
            return "text/markdown"
        return "application/octet-stream"


def test_build_index_input_files_derives_content_type_from_path() -> None:
    last_modified = datetime.now(tz=timezone.utc)
    created_at = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)

    input_files = build_index_input_files(
        {
            "note.md": LoadedFile(
                size=6,
                checksum="etag-note",
                last_modified=last_modified,
                content=b"# note",
                created_at=created_at,
            ),
            "image.png": LoadedFile(
                size=3,
                checksum="etag-image",
                last_modified=None,
                content=b"png",
            ),
        },
        content_type_provider=PathContentTypeProvider(),
    )

    assert input_files["note.md"].content_type == "text/markdown"
    assert input_files["note.md"].created_at == created_at
    assert input_files["note.md"].last_modified == last_modified
    assert input_files["note.md"].content == b"# note"
    assert input_files["image.png"].content_type == "application/octet-stream"
    assert input_files["image.png"].checksum == "etag-image"
    assert input_files["image.png"].created_at is None


def test_build_index_input_files_accepts_missing_checksum_and_content() -> None:
    input_files = build_index_input_files(
        {
            "empty.md": LoadedFile(
                size=0,
                checksum="",
                last_modified=None,
                content=None,
            ),
        },
        content_type_provider=PathContentTypeProvider(),
    )

    assert input_files["empty.md"].checksum == ""
    assert input_files["empty.md"].content is None
    assert input_files["empty.md"].created_at is None


def test_build_index_input_files_preserves_index_input_file_created_at() -> None:
    """Local readers already put created_at on IndexInputFile; adaptation must keep it (#1772)."""
    created_at = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
    last_modified = datetime(2025, 6, 1, 9, 0, tzinfo=timezone.utc)
    src = IndexInputFile(
        path="notes/kept.md",
        size=12,
        checksum="etag-kept",
        content_type="text/markdown",
        last_modified=last_modified,
        created_at=created_at,
        content=b"# kept note",
    )

    input_files = build_index_input_files(
        {"notes/kept.md": src},
        content_type_provider=PathContentTypeProvider(),
    )

    assert input_files["notes/kept.md"].created_at == created_at
    assert input_files["notes/kept.md"].last_modified == last_modified


def test_build_index_input_files_tolerates_loaders_without_created_at() -> None:
    input_files = build_index_input_files(
        {
            "legacy.md": LoadedFileWithoutCreatedAt(
                size=4,
                checksum="etag",
                last_modified=None,
                content=b"body",
            ),
        },
        content_type_provider=PathContentTypeProvider(),
    )

    assert input_files["legacy.md"].created_at is None
