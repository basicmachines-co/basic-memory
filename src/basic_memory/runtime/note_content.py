"""Portable accepted-note content contracts for Basic Memory runtimes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from basic_memory.runtime.storage import (
    NoteExternalId,
    ProjectId,
    RuntimeContentTypeSource,
    RuntimeEntityId,
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeIntegrityErrorMessage,
    RuntimeNoteActorKind,
    RuntimeNoteActorName,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
    RuntimeNoteContentVersionInput,
    runtime_content_type_is_markdown,
)

NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR = (
    "An external file change was detected before this note could be written. "
    "Refresh to review the latest content, then retry your write if you want it to win."
)


class RuntimeNoteMaterializationStatus(StrEnum):
    """Normal outcomes for materialized note file writes."""

    written = "written"
    stale = "stale"
    missing = "missing"
    conflict = "conflict"


class RuntimeAcceptedNoteWriteConflictKind(StrEnum):
    """Known accepted-note uniqueness conflicts from repository writes."""

    file_path = "file_path"
    external_id = "external_id"
    permalink = "permalink"
    generic = "generic"


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


class RuntimeAcceptedNoteEntitySource(Protocol):
    """Minimal accepted-note entity shape needed for response payloads."""

    @property
    def external_id(self) -> str: ...

    @property
    def id(self) -> RuntimeEntityId: ...

    @property
    def title(self) -> str: ...

    @property
    def note_type(self) -> str: ...

    @property
    def content_type(self) -> str: ...

    @property
    def permalink(self) -> str | None: ...

    @property
    def file_path(self) -> RuntimeFilePath: ...

    @property
    def entity_metadata(self) -> Mapping[str, object] | None: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def updated_at(self) -> datetime: ...

    @property
    def created_by(self) -> str | None: ...

    @property
    def last_updated_by(self) -> str | None: ...


class RuntimeExternalIdSource(Protocol):
    """Minimal source shape for runtime entity identity comparisons."""

    @property
    def external_id(self) -> NoteExternalId: ...


class RuntimeAcceptedNoteWriteEntitySource(RuntimeAcceptedNoteEntitySource, Protocol):
    """Accepted-note entity shape needed to plan write follow-up work."""

    @property
    def project_id(self) -> ProjectId: ...


class RuntimeDeletedNoteEntitySource(RuntimeContentTypeSource, Protocol):
    """Minimal deleted-note entity shape needed before row cleanup."""

    @property
    def external_id(self) -> object | None: ...

    @property
    def title(self) -> object | None: ...

    @property
    def permalink(self) -> object | None: ...


class RuntimeDeletedNoteEntityDeleteSource(RuntimeDeletedNoteEntitySource, Protocol):
    """Deleted-note entity shape needed for conditional row cleanup."""

    @property
    def id(self) -> RuntimeEntityId: ...


class RuntimeDeletedNoteEntityChecksumSource(Protocol):
    """Minimal deleted-note entity shape needed to guard file cleanup."""

    @property
    def checksum(self) -> object | None: ...


class RuntimeDeletedNoteFileDeleteEntitySource(
    RuntimeDeletedNoteEntityDeleteSource,
    RuntimeDeletedNoteEntityChecksumSource,
    Protocol,
):
    """Deleted-note entity shape needed to plan file cleanup after row delete."""

    @property
    def file_path(self) -> RuntimeFilePath: ...


class RuntimeDeletedNoteFileChecksumSource(Protocol):
    """Minimal note_content shape needed to guard file cleanup."""

    @property
    def file_checksum(self) -> object | None: ...


class RuntimeNoteContentStateSource(Protocol):
    """Minimal note_content row shape needed for accepted-note responses."""

    @property
    def markdown_content(self) -> str: ...

    @property
    def db_version(self) -> RuntimeNoteContentVersion: ...

    @property
    def db_checksum(self) -> RuntimeNoteContentChecksum: ...

    @property
    def file_version(self) -> int | None: ...

    @property
    def file_checksum(self) -> RuntimeFileChecksum | None: ...

    @property
    def file_write_status(self) -> str: ...

    @property
    def last_source(self) -> RuntimeNoteChangeSource | None: ...

    @property
    def file_updated_at(self) -> datetime | None: ...

    @property
    def last_materialization_error(self) -> str | None: ...


class RuntimePendingNoteMaterializationSource(Protocol):
    """Minimal note_content row shape needed to queue materialization work."""

    @property
    def db_version(self) -> RuntimeNoteContentVersionInput: ...

    @property
    def db_checksum(self) -> object: ...

    @property
    def last_source(self) -> object | None: ...


class RuntimeAcceptedNoteWriteContentSource(
    RuntimeNoteContentStateSource,
    RuntimePendingNoteMaterializationSource,
    Protocol,
):
    """Accepted note_content shape needed to plan response and write follow-up work."""


class RuntimeMaterializedNoteSource(Protocol):
    """Minimal note_content row shape needed to clean up a materialized file."""

    @property
    def file_checksum(self) -> object | None: ...


class RuntimeNoteContentDbVersionSource(Protocol):
    """Minimal note_content row shape needed to advance accepted DB versions."""

    @property
    def db_version(self) -> RuntimeNoteContentVersionInput: ...


class RuntimeNoteContentVersionSource(RuntimeNoteContentDbVersionSource, Protocol):
    """Minimal note_content row shape needed to compare accepted DB versions."""

    @property
    def db_checksum(self) -> object: ...


class RuntimeAcceptedNoteContentWriteSource(
    RuntimeNoteContentDbVersionSource,
    RuntimeMaterializedNoteSource,
    Protocol,
):
    """Minimal note_content row shape needed to plan accepted writes."""


class RuntimeNoteContentResourceEntitySource(Protocol):
    """Minimal entity shape needed for note-content resource reads."""

    @property
    def content_type(self) -> str: ...


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


class RuntimeFileChecksumReader(Protocol):
    """Capability for reading a runtime file checksum if an object exists."""

    async def exists(self, path: RuntimeFilePath) -> bool: ...

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum: ...


@dataclass(frozen=True, slots=True)
class RuntimeDeletedNoteReference:
    """Deleted note identity captured before removing its entity row."""

    external_id: NoteExternalId
    title: str
    permalink: str

    @classmethod
    def from_entity(
        cls,
        entity: RuntimeDeletedNoteEntitySource,
        *,
        file_path: RuntimeFilePath,
    ) -> Self:
        return cls(
            external_id=required_runtime_deleted_note_text(
                entity.external_id,
                field_name="external_id",
                file_path=file_path,
            ),
            title=required_runtime_deleted_note_text(
                entity.title,
                field_name="title",
                file_path=file_path,
            ),
            permalink=runtime_deleted_note_permalink(
                entity.permalink,
                file_path=file_path,
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeDeletedNoteResponse:
    """Route-facing deleted-note response assembled from typed runtime identity."""

    deleted: bool
    external_id: NoteExternalId | None = None
    title: str | None = None
    permalink: str | None = None
    file_path: RuntimeFilePath | None = None
    file_delete_status: str | None = None

    @classmethod
    def missing(cls) -> Self:
        return cls(deleted=False)

    @classmethod
    def pending_file_delete(
        cls,
        *,
        entity: RuntimeDeletedNoteEntitySource,
        file_path: RuntimeFilePath,
    ) -> Self:
        deleted_note = RuntimeDeletedNoteReference.from_entity(entity, file_path=file_path)
        return cls(
            deleted=True,
            external_id=deleted_note.external_id,
            title=deleted_note.title,
            permalink=deleted_note.permalink,
            file_path=file_path,
            file_delete_status="pending",
        )

    def as_payload(self) -> dict[str, object]:
        """Serialize to the existing delete response payload shape."""
        if not self.deleted:
            return {"deleted": False}

        if self.external_id is None:
            raise RuntimeError("Deleted note response is missing external_id")
        if self.title is None:
            raise RuntimeError("Deleted note response is missing title")
        if self.permalink is None:
            raise RuntimeError("Deleted note response is missing permalink")
        if self.file_path is None:
            raise RuntimeError("Deleted note response is missing file_path")
        if self.file_delete_status is None:
            raise RuntimeError("Deleted note response is missing file_delete_status")

        return {
            "deleted": True,
            "external_id": self.external_id,
            "title": self.title,
            "permalink": self.permalink,
            "file_path": self.file_path,
            "file_delete_status": self.file_delete_status,
        }


def select_deleted_note_file_checksum(
    *,
    note_content: RuntimeDeletedNoteFileChecksumSource | None,
    entity: RuntimeDeletedNoteEntityChecksumSource,
) -> RuntimeFileChecksum | None:
    """Choose the best accepted file checksum to guard deleted-note cleanup."""
    if note_content is not None and note_content.file_checksum is not None:
        return str(note_content.file_checksum)
    if entity.checksum is not None:
        return str(entity.checksum)
    return None


def required_runtime_deleted_note_text(
    value: object,
    *,
    field_name: str,
    file_path: RuntimeFilePath,
) -> str:
    """Return required deleted-note text for downstream live-update contracts."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Deleted entity for {file_path} is missing {field_name}")
    return value.strip()


def runtime_deleted_note_permalink(
    value: object,
    *,
    file_path: RuntimeFilePath,
) -> str:
    """Return deleted-note permalink text, falling back to the file path."""
    if isinstance(value, str) and value.strip():
        return value.strip()

    fallback = str(file_path).strip()
    if not fallback:
        raise RuntimeError(f"Deleted entity for {file_path} is missing permalink")
    return fallback


def runtime_deleted_note_reference_for_entity(
    entity: RuntimeDeletedNoteEntitySource,
    *,
    file_path: RuntimeFilePath,
) -> RuntimeDeletedNoteReference | None:
    """Return deleted-note metadata for markdown entities, not regular file entities."""
    if not runtime_content_type_is_markdown(entity):
        return None
    return RuntimeDeletedNoteReference.from_entity(entity, file_path=file_path)


@dataclass(frozen=True, slots=True)
class RuntimeNoteMaterializationResult:
    """Summary of one guarded note file materialization."""

    entity_id: RuntimeEntityId
    status: RuntimeNoteMaterializationStatus
    reason: str
    file_path: RuntimeFilePath | None = None
    file_checksum: RuntimeFileChecksum | None = None
    # True when the file was written to disk but the DB no longer owns that path
    # (the note moved or disappeared before publish), so the just-written file is
    # orphaned and must be cleaned up. Distinct from stale_db_version, where the
    # same path will be re-materialized by a newer pending version.
    written_file_orphaned: bool = False
    # True when the materialization itself succeeded but enqueueing the old-path
    # cleanup failed. The job must not fail for this — the write and its DB state
    # are already durable — but runtimes should surface it: the leftover object is
    # re-imported as a duplicate note by the next project index unless cleaned up.
    cleanup_enqueue_failed: bool = False


@dataclass(frozen=True, slots=True)
class RuntimePendingNoteFileDelete:
    """Delete job arguments captured before a note file changes ownership."""

    project_id: ProjectId
    entity_id: RuntimeEntityId
    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum | None = None
    # The note's live path after the move that scheduled this cleanup. Path
    # comparison here is exact-string (correct for object storage, where
    # case-different keys are distinct objects); a local adapter re-checks it
    # against the physical filesystem before deleting, since a case-only rename
    # on a case-insensitive filesystem aliases old and new onto the same inode.
    live_file_path: RuntimeFilePath | None = None


def plan_previous_note_file_delete(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    existing_file_path: RuntimeFilePath | None,
    accepted_file_path: RuntimeFilePath,
    file_checksum: RuntimeFileChecksum | None,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup work when an accepted note move has materialized storage."""
    if existing_file_path is None or existing_file_path == accepted_file_path:
        return None

    if file_checksum is None:
        return None

    return RuntimePendingNoteFileDelete(
        project_id=project_id,
        entity_id=entity_id,
        file_path=existing_file_path,
        file_checksum=file_checksum,
        live_file_path=accepted_file_path,
    )


def plan_previous_materialized_note_file_delete(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    existing_file_path: RuntimeFilePath | None,
    accepted_file_path: RuntimeFilePath,
    current_note_content: RuntimeMaterializedNoteSource | None,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup work when a moved note has materialized file state."""
    file_checksum = (
        str(current_note_content.file_checksum)
        if current_note_content is not None and current_note_content.file_checksum is not None
        else None
    )
    return plan_previous_note_file_delete(
        project_id=project_id,
        entity_id=entity_id,
        existing_file_path=existing_file_path,
        accepted_file_path=accepted_file_path,
        file_checksum=file_checksum,
    )


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
class RuntimePendingNoteMaterialization:
    """Materialization job arguments captured before queue submission."""

    project_id: ProjectId
    entity_id: RuntimeEntityId
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    actor_user_profile_id: UUID | None = None
    actor_kind: RuntimeNoteActorKind | None = None
    actor_name: RuntimeNoteActorName | None = None
    source: RuntimeNoteChangeSource | None = None
    cleanup_after_write: RuntimePendingNoteFileDelete | None = None


def plan_pending_note_materialization(
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    note_content: RuntimePendingNoteMaterializationSource,
    fallback_source: RuntimeNoteChangeSource,
    actor_user_profile_id: UUID | None = None,
    actor_kind: RuntimeNoteActorKind | None = None,
    actor_name: RuntimeNoteActorName | None = None,
    cleanup_after_write: RuntimePendingNoteFileDelete | None = None,
) -> RuntimePendingNoteMaterialization:
    """Build the queued materialization marker from accepted note_content state."""
    source = note_content.last_source or fallback_source
    return RuntimePendingNoteMaterialization(
        project_id=project_id,
        entity_id=entity_id,
        db_version=int(note_content.db_version),
        db_checksum=str(note_content.db_checksum),
        actor_user_profile_id=actor_user_profile_id,
        actor_kind=actor_kind,
        actor_name=actor_name,
        source=str(source) if source else None,
        cleanup_after_write=cleanup_after_write,
    )


@dataclass(frozen=True, slots=True)
class RuntimeNoteMaterializationJobRequest:
    """Queue-neutral request shape for materializing one accepted note version."""

    project_id: ProjectId
    entity_id: RuntimeEntityId
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    actor_user_profile_id: UUID | None = None
    actor_kind: RuntimeNoteActorKind | None = None
    actor_name: RuntimeNoteActorName | None = None
    source: RuntimeNoteChangeSource | None = None
    cleanup_file_path: RuntimeFilePath | None = None
    cleanup_file_checksum: RuntimeFileChecksum | None = None

    def dedupe_key(self) -> str:
        """Return the logical materialization queue identity."""
        return (
            f"materialize-note-file:{self.project_id}:"
            f"{self.entity_id}:{self.db_version}:{self.db_checksum}"
        )

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the materialization job."""
        routing_headers = dict(headers or {})
        routing_headers["project_id"] = str(self.project_id)
        return routing_headers


def note_content_matches_materialization_request(
    note_content: RuntimeNoteContentVersionSource,
    request: RuntimeNoteMaterializationJobRequest,
) -> bool:
    """Return whether note_content still matches one queued materialization request."""
    return (
        int(note_content.db_version) == request.db_version
        and str(note_content.db_checksum) == request.db_checksum
    )


def plan_note_materialization_cleanup_file_delete(
    request: RuntimeNoteMaterializationJobRequest,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup work carried by one materialization request."""
    if request.cleanup_file_path is None:
        return None
    return RuntimePendingNoteFileDelete(
        project_id=request.project_id,
        entity_id=request.entity_id,
        file_path=request.cleanup_file_path,
        file_checksum=request.cleanup_file_checksum,
    )


def plan_note_materialization_job_request(
    materialization: RuntimePendingNoteMaterialization,
) -> RuntimeNoteMaterializationJobRequest:
    """Flatten accepted note follow-up work into a queue-neutral materialization request."""
    cleanup = materialization.cleanup_after_write
    return RuntimeNoteMaterializationJobRequest(
        project_id=materialization.project_id,
        entity_id=materialization.entity_id,
        db_version=materialization.db_version,
        db_checksum=materialization.db_checksum,
        actor_user_profile_id=materialization.actor_user_profile_id,
        actor_kind=materialization.actor_kind,
        actor_name=materialization.actor_name,
        source=materialization.source,
        cleanup_file_path=cleanup.file_path if cleanup is not None else None,
        cleanup_file_checksum=cleanup.file_checksum if cleanup is not None else None,
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
    response = RuntimeDeletedNoteResponse.pending_file_delete(
        entity=entity,
        file_path=file_path,
    )
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
            cleanup_after_write=cleanup_after_write,
        ),
    )


@dataclass(frozen=True, slots=True)
class RuntimeNoteContentState:
    """Accepted note_content row state before response serialization."""

    markdown_content: str
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    file_version: int | None
    file_checksum: RuntimeFileChecksum | None
    file_write_status: str
    last_source: RuntimeNoteChangeSource | None
    file_updated_at: datetime | None
    last_materialization_error: str | None

    @classmethod
    def from_source(cls, source: RuntimeNoteContentStateSource) -> Self:
        """Build typed runtime state from a loaded note_content source row."""
        return cls(
            markdown_content=source.markdown_content,
            db_version=source.db_version,
            db_checksum=source.db_checksum,
            file_version=source.file_version,
            file_checksum=source.file_checksum,
            file_write_status=source.file_write_status,
            last_source=source.last_source,
            file_updated_at=source.file_updated_at,
            last_materialization_error=source.last_materialization_error,
        )


def plan_accepted_note_response(
    *,
    entity: RuntimeAcceptedNoteEntitySource,
    note_content: RuntimeNoteContentStateSource,
    fallback_source: RuntimeNoteChangeSource,
) -> RuntimeAcceptedNoteResponse:
    """Build an accepted-note response from accepted note_content state."""
    note_content_state = RuntimeNoteContentState.from_source(note_content)
    if note_content_state.last_source is None:
        note_content_state = replace(note_content_state, last_source=fallback_source)
    return RuntimeAcceptedNoteResponse.from_entity_and_content_state(
        entity,
        note_content_state,
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
        cleanup_after_write=cleanup_after_write,
    )


@dataclass(frozen=True, slots=True)
class RuntimeNoteContentResource:
    """Resource response state for one accepted markdown note."""

    content: str
    content_type: str

    @classmethod
    def from_entity_and_content_state(
        cls,
        entity: RuntimeNoteContentResourceEntitySource,
        note_content: RuntimeNoteContentState,
    ) -> Self:
        """Build a resource response from typed note_content state."""
        return cls(
            content=note_content.markdown_content,
            content_type=entity.content_type,
        )


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedNoteResponse:
    """Accepted note response state before HTTP serialization."""

    external_id: str
    entity_id: RuntimeEntityId
    title: str
    note_type: str
    content_type: str
    permalink: str | None
    file_path: RuntimeFilePath
    markdown_content: str
    entity_metadata: Mapping[str, object] | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    last_updated_by: str | None
    db_version: RuntimeNoteContentVersion
    db_checksum: RuntimeNoteContentChecksum
    file_version: int | None
    file_checksum: RuntimeFileChecksum | None
    file_write_status: str
    last_source: str | None
    file_updated_at: datetime | None
    last_materialization_error: str | None
    observations: tuple[Mapping[str, object], ...] = ()
    relations: tuple[Mapping[str, object], ...] = ()

    @classmethod
    def from_entity(
        cls,
        entity: RuntimeAcceptedNoteEntitySource,
        *,
        markdown_content: str,
        db_version: RuntimeNoteContentVersion,
        db_checksum: RuntimeNoteContentChecksum,
        file_version: int | None,
        file_checksum: RuntimeFileChecksum | None,
        file_write_status: str,
        last_source: str | None,
        file_updated_at: datetime | None,
        last_materialization_error: str | None,
    ) -> Self:
        """Build accepted-note response state from a loaded entity plus note_content markers."""
        return cls(
            external_id=entity.external_id,
            entity_id=entity.id,
            title=entity.title,
            note_type=entity.note_type,
            content_type=entity.content_type,
            permalink=entity.permalink,
            file_path=entity.file_path,
            markdown_content=markdown_content,
            entity_metadata=entity.entity_metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            created_by=entity.created_by,
            last_updated_by=entity.last_updated_by,
            db_version=db_version,
            db_checksum=db_checksum,
            file_version=file_version,
            file_checksum=file_checksum,
            file_write_status=file_write_status,
            last_source=last_source,
            file_updated_at=file_updated_at,
            last_materialization_error=last_materialization_error,
        )

    @classmethod
    def from_entity_and_content_state(
        cls,
        entity: RuntimeAcceptedNoteEntitySource,
        note_content: RuntimeNoteContentState,
    ) -> Self:
        """Build accepted-note response state from an entity and typed note_content state."""
        return cls.from_entity(
            entity,
            markdown_content=note_content.markdown_content,
            db_version=note_content.db_version,
            db_checksum=note_content.db_checksum,
            file_version=note_content.file_version,
            file_checksum=note_content.file_checksum,
            file_write_status=note_content.file_write_status,
            last_source=note_content.last_source,
            file_updated_at=note_content.file_updated_at,
            last_materialization_error=note_content.last_materialization_error,
        )

    def to_response_payload(self) -> dict[str, object]:
        """Serialize to the existing v2 entity-plus-note-content response shape."""
        payload: dict[str, object] = {
            "external_id": self.external_id,
            "id": self.entity_id,
            "title": self.title,
            "note_type": self.note_type,
            "content_type": self.content_type,
            "permalink": self.permalink,
            "file_path": self.file_path,
            "content": self.markdown_content,
            "entity_metadata": (
                dict(self.entity_metadata) if self.entity_metadata is not None else None
            ),
            "observations": list(self.observations),
            "relations": list(self.relations),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "created_by": self.created_by,
            "last_updated_by": self.last_updated_by,
            "api_version": "v2",
            "db_version": self.db_version,
            "db_checksum": self.db_checksum,
            "file_version": self.file_version,
            "file_checksum": self.file_checksum,
            "file_write_status": self.file_write_status,
            "last_source": self.last_source,
            "file_updated_at": (
                self.file_updated_at.isoformat() if self.file_updated_at is not None else None
            ),
            "last_materialization_error": self.last_materialization_error,
        }
        if self.file_write_status == "external_change_detected":
            payload["sync_error"] = NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR
        return payload


type RuntimeNoteContentResponsePayload = RuntimeAcceptedNoteResponse | Mapping[str, object]


def runtime_note_content_payload_as_dict(
    payload: RuntimeNoteContentResponsePayload,
) -> dict[str, object]:
    """Serialize a typed note-content payload into the existing JSON object contract."""
    if isinstance(payload, RuntimeAcceptedNoteResponse):
        return payload.to_response_payload()
    return dict(payload)


def runtime_note_content_payload_as_json_bytes(
    payload: RuntimeNoteContentResponsePayload,
) -> bytes:
    """Serialize a note-content payload for HTTP and queue transport boundaries."""
    return json.dumps(runtime_note_content_payload_as_dict(payload)).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RuntimeExpectedFileState:
    """The storage object state a guarded write expects to find."""

    file_path: RuntimeFilePath
    expected_checksum: RuntimeFileChecksum | None


@dataclass(frozen=True, slots=True)
class RuntimeFileConflict:
    """Storage object state that does not match a guarded write."""

    file_path: RuntimeFilePath
    expected_checksum: RuntimeFileChecksum | None
    actual_checksum: RuntimeFileChecksum

    @property
    def message(self) -> str:
        if self.expected_checksum is None:
            return (
                f"Refusing to overwrite unexpected file at {self.file_path}: "
                f"expected no existing object, found checksum {self.actual_checksum}"
            )
        return (
            f"Refusing to overwrite unexpected file at {self.file_path}: "
            f"expected checksum {self.expected_checksum}, found {self.actual_checksum}"
        )


class RuntimeFileConflictError(RuntimeError):
    """Raised when storage no longer matches the expected file state."""

    def __init__(self, conflict: RuntimeFileConflict) -> None:
        super().__init__(conflict.message)
        self.conflict = conflict
        self.file_path = conflict.file_path
        self.expected_checksum = conflict.expected_checksum
        self.actual_checksum = conflict.actual_checksum


async def read_runtime_file_checksum(
    reader: RuntimeFileChecksumReader,
    file_path: RuntimeFilePath,
) -> RuntimeFileChecksum | None:
    """Return the current runtime file checksum, or None when absent."""
    if not await reader.exists(file_path):
        return None
    return await reader.compute_checksum(file_path)


def runtime_file_conflict(
    actual_checksum: RuntimeFileChecksum | None,
    expected_checksum: RuntimeFileChecksum | None,
    file_path: RuntimeFilePath,
) -> RuntimeFileConflict | None:
    """Return a conflict when a present file does not match the expected checksum.

    An absent file (``actual_checksum is None``) never conflicts. A present file
    conflicts unless the caller expected exactly that checksum — a ``None``
    expectation (a fresh note that assumes no file) always conflicts with a
    present file.
    """
    if actual_checksum is None:
        return None
    if expected_checksum is None or actual_checksum != expected_checksum:
        return RuntimeFileConflict(
            file_path=file_path,
            expected_checksum=expected_checksum,
            actual_checksum=actual_checksum,
        )
    return None


async def assert_runtime_file_matches_expected(
    reader: RuntimeFileChecksumReader,
    expected: RuntimeExpectedFileState,
) -> None:
    """Raise when a guarded write would overwrite an unexpected runtime file."""
    actual_checksum = await read_runtime_file_checksum(reader, expected.file_path)
    conflict = runtime_file_conflict(
        actual_checksum, expected.expected_checksum, expected.file_path
    )
    if conflict is not None:
        raise RuntimeFileConflictError(conflict)
