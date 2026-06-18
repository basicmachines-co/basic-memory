"""Tests for portable index input-file adaptation."""

from dataclasses import dataclass
from datetime import datetime

from basic_memory.indexing.input_file_adaptation import build_index_input_files


@dataclass(frozen=True, slots=True)
class LoadedFile:
    """Storage-neutral loaded file payload for tests."""

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
    last_modified = datetime.now()

    input_files = build_index_input_files(
        {
            "note.md": LoadedFile(
                size=6,
                checksum="etag-note",
                last_modified=last_modified,
                content=b"# note",
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
    assert input_files["note.md"].created_at is None
    assert input_files["note.md"].last_modified == last_modified
    assert input_files["note.md"].content == b"# note"
    assert input_files["image.png"].content_type == "application/octet-stream"
    assert input_files["image.png"].checksum == "etag-image"


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
