from dataclasses import FrozenInstanceError

import pytest

from basic_memory.indexing import (
    FileIndexOperation,
    FileIndexResult,
    IndexFileJobResult,
    IndexFileJobStatus,
)


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


def test_index_file_job_result_carries_live_update_metadata():
    result = IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason="file indexed: notes/a.md",
        entity_id=42,
        note_external_id="note-42",
        title="A Note",
        permalink="notes/a-note",
        entity_checksum="checksum-1",
        operation=FileIndexOperation.updated,
        actor_user_profile_id="user-1",
        actor_kind="mcp_client",
        actor_name="Claude Code",
        live_update_source="mcp",
    )

    assert result.status.value == "processed"
    assert result.operation == FileIndexOperation.updated
    with pytest.raises(FrozenInstanceError):
        setattr(result, "reason", "changed")
