"""Tests for resource MIME normalization at the indexing boundary."""

from basic_memory.indexing.batch_indexer import (
    RUNTIME_RESOURCE_CONTENT_TYPE,
    regular_file_content_type,
)
from basic_memory.indexing.models import IndexInputFile


def test_markdown_mime_without_note_basename_is_persisted_as_resource() -> None:
    file = IndexInputFile(
        path="_phase7_import/.md",
        content_type="text/markdown",
        content=b"poison object",
        size=13,
    )

    assert regular_file_content_type(file) == RUNTIME_RESOURCE_CONTENT_TYPE


def test_regular_file_content_type_preserves_real_resource_mime() -> None:
    file = IndexInputFile(
        path="assets/report.pdf",
        content_type="application/pdf",
        content=b"report",
        size=6,
    )

    assert regular_file_content_type(file) == "application/pdf"
