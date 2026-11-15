"""Journal rows for note changes that arrive through storage instead of the API.

A note can change straight in storage: a WebDAV write, an rclone bisync, a direct
upload, or a local editor. The index pipeline turns each such change into a database
change: a new note_content version, a moved entity path, or a deleted entity. This
module writes the matching row into the project change journal
(``accepted_project_note_change``) inside that same transaction. Journal consumers
(the wiki projector, the OKF export, the project changes feed) then see storage
changes in the same ordered record as API writes.

Every row written here describes a change that is already in storage, so it is
written with ``materialized_at`` set. A row left unsettled would defer every later
wiki projection for the project.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models import AcceptedProjectNoteChange, Entity, NoteContent, Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)
from basic_memory.runtime.storage import (
    NoteExternalId,
    ProjectId,
    RuntimeContentType,
    RuntimeEntityId,
    RuntimeFilePath,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
    runtime_content_type_is_markdown,
)

# Journal source for changes observed in storage, so journal consumers can tell them
# apart from API writes ("api", "mcp", ...) and API deletes ("delete_note",
# "delete_directory").
ACCEPTED_STORAGE_CHANGE_SOURCE: RuntimeNoteChangeSource = "storage"


@dataclass(frozen=True, slots=True)
class StorageNoteIdentity:
    """What a journal row names about one note, read inside the change's transaction."""

    entity_id: RuntimeEntityId
    note_external_id: NoteExternalId
    permalink: str | None
    title: str
    file_path: RuntimeFilePath
    content_type: RuntimeContentType
    # The note's accepted revision, or None for a file with no note_content row.
    db_version: RuntimeNoteContentVersion | None
    db_checksum: RuntimeNoteContentChecksum | None


@dataclass(frozen=True, slots=True)
class StorageNoteChange:
    """One storage change waiting for its journal position."""

    note: StorageNoteIdentity
    operation: RuntimeProjectNoteOperation
    previous_file_path: RuntimeFilePath | None = None


async def load_storage_note_identities(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_ids: Collection[RuntimeEntityId],
) -> dict[RuntimeEntityId, StorageNoteIdentity]:
    """Read the journal identity and accepted revision of each entity that still exists.

    A delete must call this before its DELETE runs: the entity row and its cascaded
    note_content row are gone afterwards.
    """
    if not entity_ids:
        return {}
    result = await session.execute(
        select(
            Entity.id,
            Entity.external_id,
            Entity.permalink,
            Entity.title,
            Entity.file_path,
            Entity.content_type,
            NoteContent.db_version,
            NoteContent.db_checksum,
        )
        .outerjoin(NoteContent, NoteContent.entity_id == Entity.id)
        .where(
            Entity.project_id == project_id,
            Entity.id.in_(sorted(set(entity_ids))),
        )
    )
    identities: dict[RuntimeEntityId, StorageNoteIdentity] = {}
    for row in result.mappings().all():
        entity_id = int(row["id"])
        permalink = row["permalink"]
        db_version = row["db_version"]
        db_checksum = row["db_checksum"]
        identities[entity_id] = StorageNoteIdentity(
            entity_id=entity_id,
            note_external_id=str(row["external_id"]),
            permalink=str(permalink) if permalink is not None else None,
            title=str(row["title"]),
            file_path=str(row["file_path"]),
            content_type=str(row["content_type"]),
            db_version=int(db_version) if db_version is not None else None,
            db_checksum=str(db_checksum) if db_checksum is not None else None,
        )
    return identities


def _storage_note_is_journaled(note: StorageNoteIdentity) -> bool:
    """Return whether a storage change to this note gets a journal row."""
    # Non-Markdown files get no row, matching what the API journals.
    if not runtime_content_type_is_markdown(note):
        return False
    # Trigger: a Markdown note with no permalink. Databases from before permalinks
    #   became mandatory can still hold them, and a journal row needs one.
    # Why: the change is already in storage, so refusing it cannot undo it. Raising
    #   would roll back the index transaction and fail the same file on every later
    #   scan, which stalls indexing for the project.
    # Outcome: the database change goes ahead without a journal row, with a warning.
    if note.permalink is None:
        logger.warning(
            "Storage change to a note without a permalink gets no journal row",
            entity_id=note.entity_id,
            file_path=note.file_path,
        )
        return False
    return True


async def record_storage_note_changes(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    changes: Sequence[StorageNoteChange],
    project_repository: ProjectRepository | None = None,
) -> tuple[RuntimeAcceptedProjectNoteChange, ...]:
    """Write one journal row per journaled change, at consecutive positions in ``changes`` order.

    Call this as the last database write of the change's transaction. Claiming
    positions locks the project row until commit. Every journal writer takes that
    lock after its note_content and entity locks, and taking it earlier risks a
    deadlock on PostgreSQL against concurrent API writes.

    Each row has ``source="storage"``, no actor, and ``materialized_at`` equal to its
    ``accepted_at``. A batch with nothing to journal claims no position.
    """
    journaled = [change for change in changes if _storage_note_is_journaled(change.note)]
    if not journaled:
        return ()

    repository = project_repository or ProjectRepository()
    project_external_id = (
        await session.execute(select(Project.external_id).where(Project.id == project_id))
    ).scalar_one()
    head = await repository.advance_partition_position_by(session, project_id, len(journaled))
    first_position = head - len(journaled) + 1
    accepted_at = datetime.now(tz=UTC)
    rows = tuple(
        RuntimeAcceptedProjectNoteChange(
            project_id=project_id,
            project_external_id=str(project_external_id),
            partition_position=first_position + offset,
            entity_id=change.note.entity_id,
            note_external_id=change.note.note_external_id,
            permalink=str(change.note.permalink),
            title=change.note.title,
            operation=change.operation,
            file_path=change.note.file_path,
            previous_file_path=change.previous_file_path,
            accepted_at=accepted_at,
            source=ACCEPTED_STORAGE_CHANGE_SOURCE,
            db_version=change.note.db_version,
            db_checksum=change.note.db_checksum,
        )
        for offset, change in enumerate(journaled)
    )
    await repository.record_accepted_note_changes(session, rows, materialized_at=accepted_at)
    return rows


async def _note_is_in_journal(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    note_external_id: NoteExternalId,
) -> bool:
    """Return whether the journal already holds this note and its last row is not a delete."""
    latest_operation = (
        await session.execute(
            select(AcceptedProjectNoteChange.operation)
            .where(
                AcceptedProjectNoteChange.project_id == project_id,
                AcceptedProjectNoteChange.note_external_id == note_external_id,
            )
            .order_by(AcceptedProjectNoteChange.partition_position.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return latest_operation not in (None, RuntimeProjectNoteOperation.deleted.value)


@dataclass(frozen=True, slots=True)
class StorageNoteChangeJournal:
    """Journal the note_content versions that note-content reconciliation accepts from storage."""

    project_repository: ProjectRepository = field(default_factory=ProjectRepository)

    async def record_storage_note_version(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        entity_id: RuntimeEntityId,
        first_version: bool,
    ) -> RuntimeAcceptedProjectNoteChange | None:
        """Write the journal row for the note_content version this transaction just accepted.

        ``first_version`` is True when the transaction created the note's first
        note_content row. That records ``created``, unless the journal already holds
        the note: a note_content row that was lost and rebuilt records ``updated``, so
        one note never has two live ``created`` rows. Every later version records
        ``updated``. The row carries the db_version and db_checksum now in note_content.
        """
        notes = await load_storage_note_identities(
            session,
            project_id=project_id,
            entity_ids=(entity_id,),
        )
        note = notes.get(entity_id)
        if note is None:
            raise RuntimeError(
                f"Storage-accepted note_content has no entity row for entity_id={entity_id}"
            )
        operation = RuntimeProjectNoteOperation.updated
        if first_version and not await _note_is_in_journal(
            session,
            project_id=project_id,
            note_external_id=note.note_external_id,
        ):
            operation = RuntimeProjectNoteOperation.created
        rows = await record_storage_note_changes(
            session,
            project_id=project_id,
            changes=(StorageNoteChange(note=note, operation=operation),),
            project_repository=self.project_repository,
        )
        return rows[0] if rows else None
