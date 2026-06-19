from __future__ import annotations

from dataclasses import dataclass

import pytest

from basic_memory.runtime import RuntimeDeletedNoteResponse


@dataclass(frozen=True, slots=True)
class _DeletedEntity:
    external_id: object | None
    title: object | None
    permalink: object | None


def test_runtime_deleted_note_response_builds_pending_file_delete_payload() -> None:
    response = RuntimeDeletedNoteResponse.pending_file_delete(
        entity=_DeletedEntity(
            external_id=" note-1 ",
            title=" Deleted Note ",
            permalink=" notes/deleted-note ",
        ),
        file_path="notes/deleted.md",
    )

    assert response.as_payload() == {
        "deleted": True,
        "external_id": "note-1",
        "title": "Deleted Note",
        "permalink": "notes/deleted-note",
        "file_path": "notes/deleted.md",
        "file_delete_status": "pending",
    }


def test_runtime_deleted_note_response_builds_missing_payload() -> None:
    assert RuntimeDeletedNoteResponse.missing().as_payload() == {"deleted": False}


def test_runtime_deleted_note_response_rejects_missing_identity_fields() -> None:
    with pytest.raises(RuntimeError, match="missing permalink"):
        RuntimeDeletedNoteResponse.pending_file_delete(
            entity=_DeletedEntity(
                external_id="note-1",
                title="Deleted Note",
                permalink=" ",
            ),
            file_path="notes/deleted.md",
        )
