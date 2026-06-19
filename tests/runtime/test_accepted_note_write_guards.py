from __future__ import annotations

from dataclasses import dataclass

from basic_memory.runtime import (
    RUNTIME_MARKDOWN_CONTENT_TYPE,
    accepted_note_file_path_conflicts,
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
