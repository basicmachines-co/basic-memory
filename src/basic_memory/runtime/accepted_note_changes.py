"""Portable planning for accepted-note writes and post-commit changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from basic_memory.runtime.note_content_deletes import (
    RuntimeDeletedNoteFileChecksumSource,
    RuntimeDeletedNoteFileDeleteEntitySource,
    RuntimeDeletedNoteResponse,
    RuntimeMaterializedNoteSource,
    RuntimePendingNoteFileDelete,
    plan_previous_materialized_note_file_delete,
    select_deleted_note_file_checksum,
)
from basic_memory.runtime.note_content_responses import (
    RuntimeAcceptedNoteEntitySource,
    RuntimeAcceptedNoteResponse,
    RuntimeAcceptedNoteWriteEntitySource,
    RuntimeNoteContentStateSource,
    plan_accepted_note_response,
)
from basic_memory.runtime.note_materialization_planning import (
    RuntimeNoteContentDbVersionSource,
    RuntimePendingNoteMaterialization,
    RuntimePendingNoteMaterializationSource,
    plan_pending_note_materialization,
)
from basic_memory.runtime.storage import (
    NoteExternalId,
    ProjectId,
    RuntimeEntityId,
    RuntimeFilePath,
    RuntimeIntegrityErrorMessage,
    RuntimeNoteActorKind,
    RuntimeNoteActorName,
    RuntimeNoteChangeSource,
    RuntimeNoteContentVersion,
)


class RuntimeAcceptedNoteWriteConflictKind(StrEnum):
    """Known accepted-note uniqueness conflicts from repository writes."""

    file_path = "file_path"
    external_id = "external_id"
    permalink = "permalink"
    generic = "generic"


class RuntimeExternalIdSource(Protocol):
    """Minimal source shape for runtime entity identity comparisons."""

    @property
    def external_id(self) -> NoteExternalId: ...


class RuntimeAcceptedNoteContentWriteSource(
    RuntimeNoteContentDbVersionSource,
    RuntimeMaterializedNoteSource,
    Protocol,
):
    """Minimal note_content row shape needed to plan accepted writes."""


class RuntimeAcceptedNoteWriteContentSource(
    RuntimeNoteContentStateSource,
    RuntimePendingNoteMaterializationSource,
    Protocol,
):
    """Accepted note_content shape needed to plan response and write follow-up work."""


def next_runtime_note_content_version(
    current_note_content: RuntimeNoteContentDbVersionSource | None,
) -> RuntimeNoteContentVersion:
    """Return the next accepted DB version for a note_content write."""
    if current_note_content is None:
        return 1
    return int(current_note_content.db_version) + 1


def accepted_note_file_path_conflicts(
    conflicting_entity: RuntimeExternalIdSource | None,
    *,
    allowed_entity_external_id: NoteExternalId,
) -> bool:
    """Return whether a loaded entity at the target path belongs to another note."""
    return (
        conflicting_entity is not None
        and conflicting_entity.external_id != allowed_entity_external_id
    )


def classify_accepted_note_write_conflict(
    error_message: RuntimeIntegrityErrorMessage,
) -> RuntimeAcceptedNoteWriteConflictKind:
    """Classify accepted-note repository conflicts without importing a database driver."""
    normalized_message = error_message.lower()
    if "uix_entity_file_path_project" in normalized_message or (
        "file_path" in normalized_message and "project" in normalized_message
    ):
        return RuntimeAcceptedNoteWriteConflictKind.file_path
    if "external_id" in normalized_message:
        return RuntimeAcceptedNoteWriteConflictKind.external_id
    if "uix_entity_permalink_project" in normalized_message or (
        "permalink" in normalized_message and "project" in normalized_message
    ):
        return RuntimeAcceptedNoteWriteConflictKind.permalink
    return RuntimeAcceptedNoteWriteConflictKind.generic


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedNoteContentWritePlan:
    """Portable write-sequence and cleanup plan for one accepted note_content write."""

    db_version: RuntimeNoteContentVersion
    previous_file_delete: RuntimePendingNoteFileDelete | None = None


def plan_accepted_note_content_write(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    accepted_file_path: RuntimeFilePath,
    current_note_content: RuntimeAcceptedNoteContentWriteSource | None = None,
    existing_file_path: RuntimeFilePath | None = None,
) -> RuntimeAcceptedNoteContentWritePlan:
    """Plan accepted DB versioning and old-file cleanup for a note_content write."""
    return RuntimeAcceptedNoteContentWritePlan(
        db_version=next_runtime_note_content_version(current_note_content),
        previous_file_delete=plan_previous_materialized_note_file_delete(
            project_id=project_id,
            entity_id=entity_id,
            existing_file_path=existing_file_path,
            accepted_file_path=accepted_file_path,
            current_note_content=current_note_content,
        ),
    )


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedNoteChange[PayloadT]:
    """Accepted note response plus any post-commit runtime follow-up work."""

    status_code: int
    payload: PayloadT
    materialization: RuntimePendingNoteMaterialization | None = None
    file_delete: RuntimePendingNoteFileDelete | None = None


def plan_accepted_note_delete_change(
    *,
    project_id: ProjectId,
    entity: RuntimeDeletedNoteFileDeleteEntitySource | None,
    note_content: RuntimeDeletedNoteFileChecksumSource | None = None,
) -> RuntimeAcceptedNoteChange[dict[str, object]]:
    """Build the accepted delete response plus any materialized-file cleanup marker."""
    if entity is None:
        return RuntimeAcceptedNoteChange(
            status_code=200,
            payload=RuntimeDeletedNoteResponse.missing().as_payload(),
        )

    file_path = entity.file_path
    response = RuntimeDeletedNoteResponse.pending_file_delete(entity=entity, file_path=file_path)
    return RuntimeAcceptedNoteChange(
        status_code=200,
        payload=response.as_payload(),
        file_delete=RuntimePendingNoteFileDelete(
            project_id=project_id,
            entity_id=entity.id,
            file_path=file_path,
            file_checksum=select_deleted_note_file_checksum(
                note_content=note_content,
                entity=entity,
            ),
        ),
    )


def plan_accepted_note_materialization_change[PayloadT](
    *,
    status_code: int,
    payload: PayloadT,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    note_content: RuntimePendingNoteMaterializationSource,
    fallback_source: RuntimeNoteChangeSource,
    actor_user_profile_id: UUID | None = None,
    actor_kind: RuntimeNoteActorKind | None = None,
    actor_name: RuntimeNoteActorName | None = None,
    previous_file_path: RuntimeFilePath | None = None,
    cleanup_after_write: RuntimePendingNoteFileDelete | None = None,
) -> RuntimeAcceptedNoteChange[PayloadT]:
    """Build an accepted-note response plus materialization follow-up marker."""
    return RuntimeAcceptedNoteChange(
        status_code=status_code,
        payload=payload,
        materialization=plan_pending_note_materialization(
            project_id=project_id,
            entity_id=entity_id,
            note_content=note_content,
            fallback_source=fallback_source,
            actor_user_profile_id=actor_user_profile_id,
            actor_kind=actor_kind,
            actor_name=actor_name,
            previous_file_path=previous_file_path,
            cleanup_after_write=cleanup_after_write,
        ),
    )


def plan_accepted_note_response_change(
    *,
    status_code: int,
    entity: RuntimeAcceptedNoteEntitySource,
    note_content: RuntimeNoteContentStateSource,
    fallback_source: RuntimeNoteChangeSource,
) -> RuntimeAcceptedNoteChange[RuntimeAcceptedNoteResponse]:
    """Build an accepted-note response when no follow-up runtime work is needed."""
    return RuntimeAcceptedNoteChange(
        status_code=status_code,
        payload=plan_accepted_note_response(
            entity=entity,
            note_content=note_content,
            fallback_source=fallback_source,
        ),
    )


def plan_accepted_note_write_change(
    *,
    status_code: int,
    entity: RuntimeAcceptedNoteWriteEntitySource,
    note_content: RuntimeAcceptedNoteWriteContentSource,
    fallback_source: RuntimeNoteChangeSource,
    actor_user_profile_id: UUID | None = None,
    actor_kind: RuntimeNoteActorKind | None = None,
    actor_name: RuntimeNoteActorName | None = None,
    previous_file_path: RuntimeFilePath | None = None,
    cleanup_after_write: RuntimePendingNoteFileDelete | None = None,
) -> RuntimeAcceptedNoteChange[RuntimeAcceptedNoteResponse]:
    """Build the accepted-note response plus the materialization follow-up marker."""
    return plan_accepted_note_materialization_change(
        status_code=status_code,
        payload=plan_accepted_note_response(
            entity=entity,
            note_content=note_content,
            fallback_source=fallback_source,
        ),
        project_id=entity.project_id,
        entity_id=entity.id,
        note_content=note_content,
        fallback_source=fallback_source,
        actor_user_profile_id=actor_user_profile_id,
        actor_kind=actor_kind,
        actor_name=actor_name,
        previous_file_path=previous_file_path,
        cleanup_after_write=cleanup_after_write,
    )
