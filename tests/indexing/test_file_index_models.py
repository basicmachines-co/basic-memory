"""Tests for portable file-index model helpers."""

from basic_memory.indexing.models import (
    FileIndexOperation,
    file_index_operation_from_note_object_metadata,
)
from basic_memory.runtime.note_object_metadata import NOTE_OBJECT_DB_VERSION_METADATA


def test_file_index_operation_from_note_object_metadata_uses_note_db_version() -> None:
    assert (
        file_index_operation_from_note_object_metadata({NOTE_OBJECT_DB_VERSION_METADATA: "1"})
        == FileIndexOperation.created
    )
    assert (
        file_index_operation_from_note_object_metadata({NOTE_OBJECT_DB_VERSION_METADATA: "2"})
        == FileIndexOperation.updated
    )
    assert file_index_operation_from_note_object_metadata({}) is None
    assert (
        file_index_operation_from_note_object_metadata(
            {NOTE_OBJECT_DB_VERSION_METADATA: "not-a-version"}
        )
        is None
    )
