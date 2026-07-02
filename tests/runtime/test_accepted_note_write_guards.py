from __future__ import annotations

from dataclasses import dataclass

from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteWriteConflictKind,
    accepted_note_file_path_conflicts,
    classify_accepted_note_write_conflict,
)
from basic_memory.runtime.storage import (
    RUNTIME_MARKDOWN_CONTENT_TYPE,
    runtime_content_type_is_markdown,
)


@dataclass(frozen=True, slots=True)
class _Content:
    content_type: str


@dataclass(frozen=True, slots=True)
class _Entity:
    external_id: str


def test_runtime_content_type_is_markdown_accepts_markdown() -> None:
    assert runtime_content_type_is_markdown(_Content(RUNTIME_MARKDOWN_CONTENT_TYPE))


def test_runtime_content_type_is_markdown_rejects_other_types() -> None:
    assert not runtime_content_type_is_markdown(_Content("text/plain"))


def test_accepted_note_file_path_conflicts_when_path_belongs_to_another_note() -> None:
    assert accepted_note_file_path_conflicts(
        _Entity("other-note"),
        allowed_entity_external_id="target-note",
    )


def test_accepted_note_file_path_conflicts_allows_same_note() -> None:
    assert not accepted_note_file_path_conflicts(
        _Entity("target-note"),
        allowed_entity_external_id="target-note",
    )


def test_accepted_note_file_path_conflicts_allows_empty_path_lookup() -> None:
    assert not accepted_note_file_path_conflicts(
        None,
        allowed_entity_external_id="target-note",
    )


def test_classify_accepted_note_write_conflict_detects_named_file_path_constraint() -> None:
    assert (
        classify_accepted_note_write_conflict(
            'duplicate key value violates unique constraint "uix_entity_file_path_project"'
        )
        is RuntimeAcceptedNoteWriteConflictKind.file_path
    )


def test_classify_accepted_note_write_conflict_detects_generic_file_path_project_text() -> None:
    assert (
        classify_accepted_note_write_conflict("duplicate file_path within project")
        is RuntimeAcceptedNoteWriteConflictKind.file_path
    )


def test_classify_accepted_note_write_conflict_detects_external_id() -> None:
    assert (
        classify_accepted_note_write_conflict("duplicate key value violates external_id")
        is RuntimeAcceptedNoteWriteConflictKind.external_id
    )


def test_classify_accepted_note_write_conflict_detects_permalink() -> None:
    assert (
        classify_accepted_note_write_conflict(
            'duplicate key value violates unique constraint "uix_entity_permalink_project"'
        )
        is RuntimeAcceptedNoteWriteConflictKind.permalink
    )


def test_classify_accepted_note_write_conflict_defaults_to_generic() -> None:
    assert (
        classify_accepted_note_write_conflict("duplicate key value violates some other index")
        is RuntimeAcceptedNoteWriteConflictKind.generic
    )
