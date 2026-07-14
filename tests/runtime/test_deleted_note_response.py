from __future__ import annotations

from dataclasses import dataclass

import pytest

from basic_memory.runtime.note_content import (
    RuntimeDeletedNoteResponse,
    RuntimePendingNoteFileDelete,
    plan_accepted_note_delete_change,
    runtime_deleted_note_permalink,
)
from basic_memory.runtime.storage import RUNTIME_MARKDOWN_CONTENT_TYPE


@dataclass(frozen=True, slots=True)
class _DeletedEntity:
    external_id: str
    title: str
    permalink: str | None
    content_type: str = RUNTIME_MARKDOWN_CONTENT_TYPE


@dataclass(frozen=True, slots=True)
class _DeletedFileEntity:
    id: int
    external_id: str
    title: str
    permalink: str | None
    file_path: str
    checksum: str | None
    content_type: str = RUNTIME_MARKDOWN_CONTENT_TYPE


@dataclass(frozen=True, slots=True)
class _DeletedNoteContent:
    file_checksum: str | None


def test_runtime_deleted_note_response_builds_pending_file_delete_payload() -> None:
    response = RuntimeDeletedNoteResponse.pending_file_delete(
        entity=_DeletedEntity(
            external_id="note-1",
            title="Deleted Note",
            permalink="notes/deleted-note",
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


def test_runtime_deleted_note_permalink_requires_a_usable_fallback_path() -> None:
    # A markdown entity with neither a permalink nor a real file path has no
    # stable identity to publish in the delete payload.
    with pytest.raises(RuntimeError, match="missing permalink"):
        runtime_deleted_note_permalink(None, file_path=" ")


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
            external_id="note-1",
            title="Deleted Note",
            permalink="notes/deleted-note",
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
