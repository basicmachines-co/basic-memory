from __future__ import annotations

from dataclasses import dataclass

import pytest

from basic_memory.runtime.note_content import (
    RuntimeNoteContentReadAction,
    RuntimeNoteContentReadRepairStatus,
    plan_runtime_note_content_read,
    plan_runtime_note_content_read_repair,
)


@dataclass(frozen=True, slots=True)
class _Project:
    id: int


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


def test_plan_runtime_note_content_read_repair_stops_when_project_is_missing() -> None:
    plan = plan_runtime_note_content_read_repair(None, None, None)

    assert plan.status is RuntimeNoteContentReadRepairStatus.project_missing
    assert not plan.should_read_file
    assert not plan.repaired


def test_plan_runtime_note_content_read_repair_stops_when_entity_is_missing() -> None:
    project = _Project(id=7)

    plan = plan_runtime_note_content_read_repair(project, None, None)

    assert plan.status is RuntimeNoteContentReadRepairStatus.entity_missing
    assert plan.project is project
    assert not plan.should_read_file


def test_plan_runtime_note_content_read_repair_stops_for_non_markdown_entity() -> None:
    project = _Project(id=7)
    entity = _Entity(content_type="image/png")

    plan = plan_runtime_note_content_read_repair(project, entity, None)

    assert plan.status is RuntimeNoteContentReadRepairStatus.entity_missing
    assert plan.project is project
    assert plan.entity is None


def test_plan_runtime_note_content_read_repair_succeeds_when_row_already_exists() -> None:
    project = _Project(id=7)
    entity = _Entity(content_type="text/markdown")
    note_content = _NoteContent(markdown_content="# Present\n")

    plan = plan_runtime_note_content_read_repair(project, entity, note_content)

    assert plan.status is RuntimeNoteContentReadRepairStatus.already_present
    assert plan.repaired
    assert not plan.should_read_file


def test_plan_runtime_note_content_read_repair_requests_file_for_missing_markdown_row() -> None:
    project = _Project(id=7)
    entity = _Entity(content_type="text/markdown")

    plan = plan_runtime_note_content_read_repair(project, entity, None)

    assert plan.status is RuntimeNoteContentReadRepairStatus.read_file
    assert plan.should_read_file
    assert plan.require_repair_target() == (project, entity)


def test_runtime_note_content_read_repair_plan_rejects_missing_target() -> None:
    plan = plan_runtime_note_content_read_repair(_Project(id=7), None, None)

    with pytest.raises(RuntimeError, match="repair target"):
        plan.require_repair_target()
