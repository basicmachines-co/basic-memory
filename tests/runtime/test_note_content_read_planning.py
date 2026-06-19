from __future__ import annotations

from dataclasses import dataclass

import pytest

from basic_memory.runtime import (
    RuntimeNoteContentReadAction,
    plan_runtime_note_content_read,
)


@dataclass(frozen=True, slots=True)
class _Entity:
    content_type: str


@dataclass(frozen=True, slots=True)
class _NoteContent:
    markdown_content: str


def test_plan_runtime_note_content_read_returns_missing_for_absent_entity() -> None:
    plan = plan_runtime_note_content_read(None, None)

    assert plan.action is RuntimeNoteContentReadAction.missing_entity
    assert plan.entity is None
    assert plan.note_content is None


def test_plan_runtime_note_content_read_returns_metadata_for_non_markdown_entity() -> None:
    entity = _Entity(content_type="image/png")
    note_content = _NoteContent(markdown_content="# Ignored\n")

    plan = plan_runtime_note_content_read(entity, note_content)

    assert plan.action is RuntimeNoteContentReadAction.entity_metadata
    assert plan.require_entity_metadata() is entity
    assert plan.note_content is None


def test_plan_runtime_note_content_read_returns_missing_for_markdown_without_content() -> None:
    entity = _Entity(content_type="text/markdown")

    plan = plan_runtime_note_content_read(entity, None)

    assert plan.action is RuntimeNoteContentReadAction.missing_note_content
    assert plan.entity is entity
    assert plan.note_content is None


def test_plan_runtime_note_content_read_returns_accepted_note_for_markdown_content() -> None:
    entity = _Entity(content_type="text/markdown")
    note_content = _NoteContent(markdown_content="# Accepted\n")

    plan = plan_runtime_note_content_read(entity, note_content)

    assert plan.action is RuntimeNoteContentReadAction.accepted_note
    assert plan.require_accepted_note() == (entity, note_content)


def test_runtime_note_content_read_plan_rejects_wrong_accessor() -> None:
    plan = plan_runtime_note_content_read(_Entity(content_type="text/markdown"), None)

    with pytest.raises(RuntimeError, match="metadata-only entity"):
        plan.require_entity_metadata()

    with pytest.raises(RuntimeError, match="accepted note content"):
        plan.require_accepted_note()
