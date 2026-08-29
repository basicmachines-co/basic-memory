"""Portable planning for accepted-note writes and post-commit changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Protocol, TypeVar
from uuid import UUID

from basic_memory.runtime.note_content_deletes import (
    RuntimeDeletedNoteFileChecksumSource,
    RuntimeDeletedNoteFileDeleteEntitySource,
    RuntimeDeletedNoteResponse,
    RuntimeMaterializedNoteSource,
    RuntimePendingNoteFileDelete,
    plan_previous_note_file_delete,
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
    RuntimeFileChecksum,
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

    @property
    def db_checksum(self) -> RuntimeFileChecksum:
        """Return the accepted checksum that identifies the source bytes."""

    @property
    def file_version(self) -> RuntimeNoteContentVersion | None: ...

    @property
    def file_write_status(self) -> str: ...


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


def select_accepted_note_source_checksum(
    current_note_content: RuntimeAcceptedNoteContentWriteSource,
    *,
    observed_file_checksum: RuntimeFileChecksum | None = None,
) -> RuntimeFileChecksum | None:
    """Select the checksum for source bytes vacated by an accepted path change.

    ``accept_write`` deliberately preserves the previously published file fields while the next
    DB version is pending. During ``pending``, ``writing``, ``failed``, or
    ``external_change_detected``, storage may contain either known version or unrelated bytes. A
    live checksum matching either known version resolves that ambiguity. An absent or unexpected
    object proves neither version owns the path, so cleanup must remain unclaimed. A synchronized
    file version identifies the expected checksum, but storage observation still proves that those
    bytes exist at the source path before cleanup ownership is recorded.
    """
    if current_note_content.file_write_status in {
        "pending",
        "writing",
        "failed",
        "external_change_detected",
    }:
        if observed_file_checksum in {
            current_note_content.db_checksum,
            current_note_content.file_checksum,
        }:
            return observed_file_checksum
        return None

    file_version_is_current = (
        current_note_content.file_write_status == "synced"
        and current_note_content.file_version is not None
        and int(current_note_content.file_version) == int(current_note_content.db_version)
    )
    if (
        file_version_is_current
        and current_note_content.file_checksum is not None
        and observed_file_checksum == current_note_content.file_checksum
    ):
        return observed_file_checksum
    return None


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
    source_file_checksum: RuntimeFileChecksum | None = None,
) -> RuntimeAcceptedNoteContentWritePlan:
    """Plan accepted DB versioning and old-file cleanup for a note_content write."""
    source_checksum = (
        select_accepted_note_source_checksum(
            current_note_content,
            observed_file_checksum=source_file_checksum,
        )
        if current_note_content is not None
        else None
    )
    return RuntimeAcceptedNoteContentWritePlan(
        db_version=next_runtime_note_content_version(current_note_content),
        previous_file_delete=plan_previous_note_file_delete(
            project_id=project_id,
            entity_id=entity_id,
            existing_file_path=existing_file_path,
            accepted_file_path=accepted_file_path,
            file_checksum=source_checksum,
        ),
    )


_PayloadT_co = TypeVar("_PayloadT_co", covariant=True)


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedNoteChange(Generic[_PayloadT_co]):
    """Accepted note response plus any post-commit runtime follow-up work."""

    status_code: int
    payload: _PayloadT_co
    materialization: RuntimePendingNoteMaterialization | None = None
    file_delete: RuntimePendingNoteFileDelete | None = None
    # Surviving notes whose relations pointed at a deleted target. Their search
    # rows still carry the deleted entity's id/title, so the runtime re-indexes
    # them after commit — the same repair the watcher and directory delete paths
    # run (#1351). Empty for every non-delete change.
    relation_cleanup_entity_ids: frozenset[RuntimeEntityId] = frozenset()


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
