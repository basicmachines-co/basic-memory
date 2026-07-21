"""Portable accepted-note materialization planning and queue contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from basic_memory.runtime.note_content_deletes import RuntimePendingNoteFileDelete
from basic_memory.runtime.storage import (
    ProjectId,
    RuntimeEntityId,
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeNoteActorKind,
    RuntimeNoteActorName,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
    RuntimeNoteContentVersionInput,
)


class RuntimeNoteMaterializationStatus(StrEnum):
    """Normal outcomes for materialized note file writes."""

    written = "written"
    stale = "stale"
    missing = "missing"
    conflict = "conflict"


class RuntimePendingNoteMaterializationSource(Protocol):
    """Minimal note_content row shape needed to queue materialization work.

    Input-typed on purpose: downstream runtimes replay these values from
    persisted job payloads where a driver may deliver a string, so planning
    coerces instead of trusting the declared shape.
    """

    @property
    def db_version(self) -> RuntimeNoteContentVersionInput: ...

    @property
    def db_checksum(self) -> object: ...

    @property
    def last_source(self) -> object | None: ...


class RuntimeNoteContentDbVersionSource(Protocol):
    """Minimal note_content row shape needed to advance accepted DB versions.

    db_version stays input-typed because downstream runtimes may replay a
    string value from a persisted job payload.
    """

    @property
    def db_version(self) -> RuntimeNoteContentVersionInput: ...


class RuntimeNoteContentVersionSource(RuntimeNoteContentDbVersionSource, Protocol):
    """Minimal note_content row shape needed to compare accepted DB versions."""

    @property
    def db_checksum(self) -> object: ...


@dataclass(frozen=True, slots=True)
class RuntimeNoteMaterializationResult:
    """Summary of one guarded note file materialization."""

    entity_id: RuntimeEntityId
    status: RuntimeNoteMaterializationStatus
    reason: str
    file_path: RuntimeFilePath | None = None
    file_checksum: RuntimeFileChecksum | None = None
    # A write can succeed after the note moved or disappeared; the resulting
    # orphan must be cleaned rather than reported as an ordinary stale version.
    written_file_orphaned: bool = False
    # Cleanup enqueue failure is surfaced separately because the accepted write
    # and DB state are already durable and must not be rolled back.
    cleanup_enqueue_failed: bool = False


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
    previous_file_path: RuntimeFilePath | None = None
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
    previous_file_path: RuntimeFilePath | None = None,
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
        previous_file_path=previous_file_path,
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
    previous_file_path: RuntimeFilePath | None = None
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
        previous_file_path=materialization.previous_file_path,
        cleanup_file_path=cleanup.file_path if cleanup is not None else None,
        cleanup_file_checksum=cleanup.file_checksum if cleanup is not None else None,
    )
