from __future__ import annotations

from dataclasses import dataclass

import pytest

from basic_memory.runtime.note_content import (
    RuntimeDeletedNoteResponse,
    RuntimePendingNoteFileDelete,
    plan_accepted_note_delete_change,
)
from basic_memory.runtime.storage import RUNTIME_MARKDOWN_CONTENT_TYPE


@dataclass(frozen=True, slots=True)
class _DeletedEntity:
    external_id: object | None
    title: object | None
    permalink: object | None
    content_type: str = RUNTIME_MARKDOWN_CONTENT_TYPE


@dataclass(frozen=True, slots=True)
class _DeletedFileEntity:
    id: int
    external_id: object | None
    title: object | None
    permalink: object | None
    file_path: str
    checksum: object | None
    content_type: str = RUNTIME_MARKDOWN_CONTENT_TYPE


@dataclass(frozen=True, slots=True)
class _DeletedNoteContent:
    file_checksum: object | None


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


def test_runtime_deleted_note_response_uses_file_path_when_permalink_is_missing() -> None:
    response = RuntimeDeletedNoteResponse.pending_file_delete(
        entity=_DeletedEntity(
            external_id="note-1",
            title="Deleted Note",
            permalink=" ",
        ),
        file_path="notes/deleted.md",
    )

    assert response.as_payload() == {
        "deleted": True,
        "external_id": "note-1",
        "title": "Deleted Note",
        "permalink": "notes/deleted.md",
        "file_path": "notes/deleted.md",
        "file_delete_status": "pending",
    }


@pytest.mark.parametrize(
    ("entity", "message"),
    [
        (
            _DeletedEntity(
                external_id=None,
                title="Deleted Note",
                permalink="notes/deleted-note",
            ),
            "missing external_id",
        ),
        (
            _DeletedEntity(
                external_id="note-1",
                title=" ",
                permalink="notes/deleted-note",
            ),
            "missing title",
        ),
    ],
)
def test_runtime_deleted_note_response_rejects_missing_identity_fields(
    entity: _DeletedEntity,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        RuntimeDeletedNoteResponse.pending_file_delete(
            entity=entity,
            file_path="notes/deleted.md",
        )


def test_plan_accepted_note_delete_change_builds_missing_payload() -> None:
    accepted = plan_accepted_note_delete_change(
        project_id=7,
        entity=None,
    )

    assert accepted.status_code == 200
    assert accepted.payload == {"deleted": False}
    assert accepted.materialization is None
    assert accepted.file_delete is None


def test_plan_accepted_note_delete_change_builds_payload_and_file_cleanup() -> None:
    accepted = plan_accepted_note_delete_change(
        project_id=7,
        entity=_DeletedFileEntity(
            id=42,
            external_id=" note-1 ",
            title=" Deleted Note ",
            permalink=" notes/deleted-note ",
            file_path="notes/deleted.md",
            checksum="entity-checksum",
        ),
        note_content=_DeletedNoteContent(file_checksum="note-file-checksum"),
    )

    assert accepted.status_code == 200
    assert accepted.payload == {
        "deleted": True,
        "external_id": "note-1",
        "title": "Deleted Note",
        "permalink": "notes/deleted-note",
        "file_path": "notes/deleted.md",
        "file_delete_status": "pending",
    }
    assert accepted.materialization is None
    assert accepted.file_delete == RuntimePendingNoteFileDelete(
        project_id=7,
        entity_id=42,
        file_path="notes/deleted.md",
        file_checksum="note-file-checksum",
    )
