"""Portable read and read-repair planning for accepted note content."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from basic_memory.runtime.storage import RuntimeContentTypeSource, runtime_content_type_is_markdown


class RuntimeNoteContentReadAction(StrEnum):
    """Read outcomes for tenant note-content projections."""

    missing_entity = "missing_entity"
    missing_note_content = "missing_note_content"
    entity_metadata = "entity_metadata"
    accepted_note = "accepted_note"


class RuntimeNoteContentReadRepairStatus(StrEnum):
    """Read-repair outcomes for missing accepted note_content rows."""

    project_missing = "project_missing"
    entity_missing = "entity_missing"
    already_present = "already_present"
    read_file = "read_file"
    file_missing = "file_missing"
    empty_file = "empty_file"
    s3_error = "s3_error"
    repaired = "repaired"


@dataclass(frozen=True, slots=True)
class RuntimeNoteContentReadPlan[EntityT, NoteContentT]:
    """Typed read-side decision for one entity plus optional accepted note_content."""

    action: RuntimeNoteContentReadAction
    entity: EntityT | None = None
    note_content: NoteContentT | None = None

    def require_entity_metadata(self) -> EntityT:
        """Return the entity for metadata-only responses."""
        if self.action is not RuntimeNoteContentReadAction.entity_metadata or self.entity is None:
            raise RuntimeError("note-content read plan does not contain metadata-only entity")
        return self.entity

    def require_accepted_note(self) -> tuple[EntityT, NoteContentT]:
        """Return the entity and note_content for accepted markdown responses."""
        if (
            self.action is not RuntimeNoteContentReadAction.accepted_note
            or self.entity is None
            or self.note_content is None
        ):
            raise RuntimeError("note-content read plan does not contain accepted note content")
        return self.entity, self.note_content


@dataclass(frozen=True, slots=True)
class RuntimeNoteContentReadRepairPlan[ProjectT, EntityT]:
    """Typed DB preflight plan for repairing missing note_content from storage."""

    status: RuntimeNoteContentReadRepairStatus
    project: ProjectT | None = None
    entity: EntityT | None = None

    @property
    def should_read_file(self) -> bool:
        """Return whether the adapter should try loading the canonical file."""
        return self.status is RuntimeNoteContentReadRepairStatus.read_file

    @property
    def repaired(self) -> bool:
        """Return whether read repair has a usable note_content row after this plan."""
        return self.status is RuntimeNoteContentReadRepairStatus.already_present

    def require_repair_target(self) -> tuple[ProjectT, EntityT]:
        """Return the project/entity pair needed to read and reconcile storage content."""
        if (
            self.status is not RuntimeNoteContentReadRepairStatus.read_file
            or self.project is None
            or self.entity is None
        ):
            raise RuntimeError("note-content read repair plan does not contain a repair target")
        return self.project, self.entity


def plan_runtime_note_content_read[EntityT: RuntimeContentTypeSource, NoteContentT](
    entity: EntityT | None,
    note_content: NoteContentT | None,
) -> RuntimeNoteContentReadPlan[EntityT, NoteContentT]:
    """Plan how a note-content read should project one loaded entity."""
    if entity is None:
        return RuntimeNoteContentReadPlan(action=RuntimeNoteContentReadAction.missing_entity)

    if not runtime_content_type_is_markdown(entity):
        return RuntimeNoteContentReadPlan(
            action=RuntimeNoteContentReadAction.entity_metadata,
            entity=entity,
        )

    if note_content is None:
        return RuntimeNoteContentReadPlan(
            action=RuntimeNoteContentReadAction.missing_note_content,
            entity=entity,
        )

    return RuntimeNoteContentReadPlan(
        action=RuntimeNoteContentReadAction.accepted_note,
        entity=entity,
        note_content=note_content,
    )


def plan_runtime_note_content_read_repair[ProjectT, EntityT: RuntimeContentTypeSource](
    project: ProjectT | None,
    entity: EntityT | None,
    note_content: object | None,
) -> RuntimeNoteContentReadRepairPlan[ProjectT, EntityT]:
    """Plan the DB side of read repair before an adapter reads storage."""
    if project is None:
        return RuntimeNoteContentReadRepairPlan(
            status=RuntimeNoteContentReadRepairStatus.project_missing
        )

    if entity is None or not runtime_content_type_is_markdown(entity):
        return RuntimeNoteContentReadRepairPlan(
            status=RuntimeNoteContentReadRepairStatus.entity_missing,
            project=project,
        )

    if note_content is not None:
        return RuntimeNoteContentReadRepairPlan(
            status=RuntimeNoteContentReadRepairStatus.already_present,
            project=project,
            entity=entity,
        )

    return RuntimeNoteContentReadRepairPlan(
        status=RuntimeNoteContentReadRepairStatus.read_file,
        project=project,
        entity=entity,
    )
