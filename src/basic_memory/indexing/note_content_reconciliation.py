"""Pure note-content reconciliation rules shared by indexing runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

type NoteContentChecksum = str
type NoteContentSource = str
type NoteContentWriteStatus = Literal[
    "pending",
    "writing",
    "synced",
    "failed",
    "external_change_detected",
]


@dataclass(frozen=True, slots=True)
class ObservedNoteContent:
    """One observed markdown file version ready to compare against note_content."""

    markdown_content: str
    checksum: NoteContentChecksum
    observed_at: datetime
    source: NoteContentSource


@dataclass(frozen=True, slots=True)
class NoteContentState:
    """Current note_content DB/file version state."""

    db_version: int
    db_checksum: NoteContentChecksum
    file_version: int | None = None
    file_checksum: NoteContentChecksum | None = None


@dataclass(frozen=True, slots=True)
class AcceptedNoteContentVersion:
    """Accepted DB note-content marker carried by materialization work."""

    db_version: int
    db_checksum: NoteContentChecksum


@dataclass(frozen=True, slots=True)
class MaterializedNoteContentFile:
    """One file object written from a DB-accepted note_content version."""

    db_version: int
    db_checksum: NoteContentChecksum
    file_checksum: NoteContentChecksum
    file_updated_at: datetime
    attempted_at: datetime


@dataclass(frozen=True, slots=True)
class NoteContentBootstrap:
    """Initial state for a note_content row created from an observed file."""

    markdown_content: str
    db_version: int
    db_checksum: NoteContentChecksum
    file_version: int
    file_checksum: NoteContentChecksum
    file_write_status: NoteContentWriteStatus
    last_source: NoteContentSource
    updated_at: datetime
    file_updated_at: datetime
    last_materialization_error: str | None
    last_materialization_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class NoteContentFileSynced:
    """Update when the observed file caught up to the accepted DB content."""

    markdown_content: str
    file_version: int
    file_checksum: NoteContentChecksum
    file_write_status: NoteContentWriteStatus
    file_updated_at: datetime
    last_materialization_error: str | None
    last_materialization_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class NoteContentFileObserved:
    """Update when an older materialized file is seen while DB is ahead."""

    file_version: int
    file_checksum: NoteContentChecksum
    file_updated_at: datetime


@dataclass(frozen=True, slots=True)
class NoteContentMaterializedCurrent:
    """Update when a materialized file still matches the latest accepted DB note."""

    file_version: int
    file_checksum: NoteContentChecksum
    file_write_status: NoteContentWriteStatus
    file_updated_at: datetime
    last_materialization_error: str | None
    last_materialization_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class NoteContentMaterializedStale:
    """Update when a written file is already behind a newer accepted DB note."""

    file_version: int
    file_checksum: NoteContentChecksum
    file_write_status: NoteContentWriteStatus
    file_updated_at: datetime
    last_materialization_error: str | None
    last_materialization_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class NoteContentMaterializationStatusUpdate:
    """Update when current materialization work fails or detects a conflict."""

    file_write_status: NoteContentWriteStatus
    file_checksum: NoteContentChecksum | None
    last_materialization_error: str | None
    last_materialization_attempt_at: datetime


@dataclass(frozen=True, slots=True)
class NoteContentPromoted:
    """Update when an external file change becomes the newest accepted content."""

    markdown_content: str
    db_version: int
    db_checksum: NoteContentChecksum
    file_version: int
    file_checksum: NoteContentChecksum
    file_write_status: NoteContentWriteStatus
    last_source: NoteContentSource
    updated_at: datetime
    file_updated_at: datetime
    last_materialization_error: str | None
    last_materialization_attempt_at: datetime | None


type NoteContentReconciliationPlan = (
    NoteContentBootstrap | NoteContentFileSynced | NoteContentFileObserved | NoteContentPromoted
)
type NoteContentMaterializationPublishPlan = (
    NoteContentMaterializedCurrent | NoteContentMaterializedStale
)
type NoteContentMaterializationStatusPlan = NoteContentMaterializationStatusUpdate | None


def note_content_matches_accepted_version(
    current: NoteContentState,
    accepted: AcceptedNoteContentVersion,
) -> bool:
    """Return whether note_content still describes the accepted DB marker."""
    return current.db_version == accepted.db_version and current.db_checksum == accepted.db_checksum


def plan_note_content_reconciliation(
    current: NoteContentState | None,
    observed: ObservedNoteContent,
) -> NoteContentReconciliationPlan:
    """Choose the note_content write needed to converge one observed file.

    The same rule must apply everywhere:
    - observed checksum == DB checksum: the file now matches accepted DB content
    - observed checksum == previous file checksum while versions differ: DB stays ahead
    - otherwise: the observed file wins the next DB/file version
    """
    if current is None:
        return NoteContentBootstrap(
            markdown_content=observed.markdown_content,
            db_version=1,
            db_checksum=observed.checksum,
            file_version=1,
            file_checksum=observed.checksum,
            file_write_status="synced",
            last_source=observed.source,
            updated_at=observed.observed_at,
            file_updated_at=observed.observed_at,
            last_materialization_error=None,
            last_materialization_attempt_at=None,
        )

    if observed.checksum == current.db_checksum:
        return NoteContentFileSynced(
            markdown_content=observed.markdown_content,
            file_version=current.db_version,
            file_checksum=observed.checksum,
            file_write_status="synced",
            file_updated_at=observed.observed_at,
            last_materialization_error=None,
            last_materialization_attempt_at=None,
        )

    if (
        current.file_version is not None
        and current.file_checksum is not None
        and current.file_version != current.db_version
        and observed.checksum == current.file_checksum
    ):
        return NoteContentFileObserved(
            file_version=current.file_version,
            file_checksum=current.file_checksum,
            file_updated_at=observed.observed_at,
        )

    next_version = max(current.db_version, current.file_version or 0) + 1
    return NoteContentPromoted(
        markdown_content=observed.markdown_content,
        db_version=next_version,
        db_checksum=observed.checksum,
        file_version=next_version,
        file_checksum=observed.checksum,
        file_write_status="synced",
        last_source=observed.source,
        updated_at=observed.observed_at,
        file_updated_at=observed.observed_at,
        last_materialization_error=None,
        last_materialization_attempt_at=None,
    )


def plan_note_content_materialization_publish(
    *,
    current: NoteContentState,
    written: MaterializedNoteContentFile,
) -> NoteContentMaterializationPublishPlan:
    """Choose how note_content should record one completed materialization write."""
    payload_is_current = note_content_matches_accepted_version(
        current,
        AcceptedNoteContentVersion(
            db_version=written.db_version,
            db_checksum=written.db_checksum,
        ),
    )
    if payload_is_current:
        return NoteContentMaterializedCurrent(
            file_version=written.db_version,
            file_checksum=written.file_checksum,
            file_write_status="synced",
            file_updated_at=written.file_updated_at,
            last_materialization_error=None,
            last_materialization_attempt_at=written.attempted_at,
        )

    return NoteContentMaterializedStale(
        file_version=written.db_version,
        file_checksum=written.file_checksum,
        file_write_status="pending",
        file_updated_at=written.file_updated_at,
        last_materialization_error=None,
        last_materialization_attempt_at=written.attempted_at,
    )


def plan_note_content_materialization_status(
    *,
    current: NoteContentState,
    accepted: AcceptedNoteContentVersion,
    file_write_status: NoteContentWriteStatus,
    attempted_at: datetime,
    actual_file_checksum: NoteContentChecksum | None = None,
    error_message: str | None = None,
) -> NoteContentMaterializationStatusPlan:
    """Choose whether current materialization status should update note_content."""
    if not note_content_matches_accepted_version(current, accepted):
        return None

    return NoteContentMaterializationStatusUpdate(
        file_write_status=file_write_status,
        file_checksum=actual_file_checksum,
        last_materialization_error=error_message,
        last_materialization_attempt_at=attempted_at,
    )
