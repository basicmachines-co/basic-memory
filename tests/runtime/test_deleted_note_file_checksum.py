from __future__ import annotations

from dataclasses import dataclass

from basic_memory.runtime.note_content import select_deleted_note_file_checksum


@dataclass(frozen=True, slots=True)
class _NoteContentFileState:
    file_checksum: object | None


@dataclass(frozen=True, slots=True)
class _EntityFileState:
    checksum: object | None


def test_select_deleted_note_file_checksum_prefers_materialized_note_content() -> None:
    file_checksum = select_deleted_note_file_checksum(
        note_content=_NoteContentFileState(file_checksum="note-file-checksum"),
        entity=_EntityFileState(checksum="entity-checksum"),
    )

    assert file_checksum == "note-file-checksum"


def test_select_deleted_note_file_checksum_falls_back_to_entity_checksum() -> None:
    file_checksum = select_deleted_note_file_checksum(
        note_content=_NoteContentFileState(file_checksum=None),
        entity=_EntityFileState(checksum="entity-checksum"),
    )

    assert file_checksum == "entity-checksum"


def test_select_deleted_note_file_checksum_allows_unguarded_cleanup() -> None:
    assert (
        select_deleted_note_file_checksum(
            note_content=None,
            entity=_EntityFileState(checksum=None),
        )
        is None
    )
