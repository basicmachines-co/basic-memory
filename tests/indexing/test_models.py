from dataclasses import FrozenInstanceError

import pytest

from basic_memory.indexing import FileIndexOperation, FileIndexResult


def test_file_index_result_is_a_frozen_success_value():
    result = FileIndexResult(
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        checksum="checksum-1",
        operation=FileIndexOperation.created,
    )

    assert result.operation.value == "created"
    assert result.file_path == "notes/a.md"
    with pytest.raises(FrozenInstanceError):
        setattr(result, "checksum", "checksum-2")
