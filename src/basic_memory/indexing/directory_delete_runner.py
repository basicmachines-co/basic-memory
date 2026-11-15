"""Portable directory-delete result and cleanup enqueue orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, NotRequired, Protocol, TypedDict

from sqlalchemy import bindparam, delete, exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models import Entity, NoteContent, Project, Relation
from basic_memory.markdown.note_lock import LOCKED_NOTE_MESSAGE, note_is_locked
from basic_memory.repository.accepted_note_vector_cleanup import (
    ProjectIndexExternalVectorCleaner,
    delete_project_index_vector_rows,
)
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.relation_repository import (
    lock_note_content_before_entity_mutation,
)
from basic_memory.runtime.cleanup import (
    RuntimeDeleteStatus,
    RuntimeDirectoryFileSnapshot,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    plan_directory_file_snapshot,
    plan_note_file_delete_job_request,
)
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)
from basic_memory.runtime.storage import (
    ProjectExternalId,
    ProjectId,
    RuntimeEntityId,
    RuntimeFilePath,
    RuntimeNoteChangeSource,
    runtime_content_type_is_markdown,
)
from basic_memory.utils import valid_project_path_value

type DirectoryDeleteFileStatus = Literal["complete", "pending", "failed"]
type DirectoryFileLockCheck = Callable[[Sequence[RuntimeDirectoryFileSnapshot]], Awaitable[None]]
type DirectoryDeleteProjectChanges = Mapping[RuntimeEntityId, RuntimeAcceptedProjectNoteChange]

# Journal source for notes removed by a folder delete, so journal consumers can tell
# them apart from single-note deletes ("delete_note").
ACCEPTED_DIRECTORY_DELETE_SOURCE: RuntimeNoteChangeSource = "delete_directory"


class DirectoryDeleteRejectKind(StrEnum):
    """Portable directory-delete rejection categories."""

    bad_request = "bad_request"
    not_found = "not_found"
    locked = "locked"

    @property
    def http_status_code(self) -> int:
        """Return the route status that matches this rejection behavior."""
        match self:
            case DirectoryDeleteRejectKind.bad_request:
                return 400
            case DirectoryDeleteRejectKind.not_found:
                return 404
            case DirectoryDeleteRejectKind.locked:
                return 423


@dataclass(frozen=True, slots=True)
class DirectoryDeleteRejection:
    """Typed rejection from directory-delete acceptance."""

    kind: DirectoryDeleteRejectKind
    detail: str


class DirectoryDeleteRejected(Exception):
    """Exception wrapper for a typed directory-delete rejection."""

    def __init__(self, rejection: DirectoryDeleteRejection) -> None:
        super().__init__(rejection.detail)
        self.rejection = rejection


class DirectoryFileDeleteEnqueueError(Exception):
    """Raised by adapters for expected cleanup queue submission failures."""


@dataclass(frozen=True, slots=True)
class DirectoryDeleteAcceptanceRequest:
    """Input for accepting a directory delete before cleanup jobs run."""

    project_external_id: ProjectExternalId
    directory: RuntimeFilePath


@dataclass(frozen=True, slots=True)
class DirectoryDeleteAcceptance:
    """Accepted DB-side directory delete before cleanup jobs run."""

    project_id: ProjectId
    files: tuple[RuntimeDirectoryFileSnapshot, ...]
    # Surviving entities OUTSIDE the deleted directory that linked into it: their
    # search_index relation rows go stale once the deleted rows disappear, so the
    # caller reindexes these sources to drop the dangling relation rows.
    relation_cleanup_entity_ids: frozenset[int] = frozenset()
    # One `deleted` journal row per removed Markdown note, keyed by entity id. Each
    # rides on its file's cleanup request so the cleanup can settle it.
    project_changes: DirectoryDeleteProjectChanges = field(default_factory=dict)

    @property
    def deleted_files(self) -> tuple[RuntimeFilePath, ...]:
        return tuple(file_snapshot.file_path for file_snapshot in self.files)


@dataclass(frozen=True, slots=True)
class DirectoryEntityDeleteResult:
    """Rows accepted by the guarded directory delete and their repair work."""

    deleted_entity_ids: frozenset[int] = frozenset()
    relation_cleanup_entity_ids: frozenset[int] = frozenset()
    # Journal rows written in the delete transaction, keyed by deleted entity id.
    project_changes: DirectoryDeleteProjectChanges = field(default_factory=dict)


class DirectoryDeleteAcceptanceStore(Protocol):
    """Repository capability for accepting directory deletes into DB state."""

    async def load_project_id(
        self,
        session: AsyncSession,
        project_external_id: ProjectExternalId,
    ) -> ProjectId | None:
        """Return the database id for a project external id."""

    async def load_directory_file_snapshots(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        directory: RuntimeFilePath,
    ) -> Sequence[RuntimeDirectoryFileSnapshot]:
        """Return file snapshots owned by the accepted directory delete."""

    async def delete_directory_entities(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        project_external_id: ProjectExternalId,
        directory: RuntimeFilePath,
        entity_ids: Sequence[int],
    ) -> DirectoryEntityDeleteResult:
        """Delete current directory members and return accepted rows plus repair work.

        Relation cleanup ids are entities OUTSIDE the deleted set whose relations
        pointed into it; their search rows need reindexing to drop dangling relations.
        Each deleted Markdown note gets a `deleted` journal row in the same transaction.
        """


class DirectoryDeleteRelationCleanupRefresher(Protocol):
    """Capability that reindexes surviving relation sources after an accepted delete.

    Sources deleted between acceptance and the refresh are skipped, not fatal —
    their search rows disappeared with them.
    """

    async def refresh_relation_sources(self, entity_ids: Sequence[int]) -> None: ...


@dataclass(frozen=True, slots=True)
class DirectoryDeleteRuntime:
    """Dependencies required by directory-delete acceptance orchestration."""

    store: DirectoryDeleteAcceptanceStore
    file_delete_enqueuer: DirectoryFileDeleteEnqueuer
    # None means this runtime has no inline reindex path; the surviving source ids
    # are still surfaced on the accepted result for deferred (queued) consumers.
    relation_cleanup_refresher: DirectoryDeleteRelationCleanupRefresher | None = None
    check_file_locks: DirectoryFileLockCheck | None = None


@dataclass(frozen=True, slots=True)
class RepositoryDirectoryDeleteAcceptanceStore:
    """Repository-backed directory-delete acceptance store."""

    external_vector_cleaner: ProjectIndexExternalVectorCleaner | None = None

    async def load_project_id(
        self,
        session: AsyncSession,
        project_external_id: ProjectExternalId,
    ) -> ProjectId | None:
        result = await session.execute(
            select(Project.id).where(Project.external_id == project_external_id).limit(1)
        )
        project_id = result.scalars().one_or_none()
        return int(project_id) if project_id is not None else None

    async def load_directory_file_snapshots(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        directory: RuntimeFilePath,
    ) -> Sequence[RuntimeDirectoryFileSnapshot]:
        query = (
            select(
                Entity.id,
                Entity.file_path,
                Entity.checksum,
                Entity.mtime,
                Entity.size,
                Entity.updated_at,
                NoteContent.file_checksum.label("note_file_checksum"),
                NoteContent.file_updated_at.label("note_file_updated_at"),
            )
            .outerjoin(NoteContent, NoteContent.entity_id == Entity.id)
            .where(Entity.project_id == project_id)
        )
        if directory not in {"", "/"}:
            escaped_directory = directory_delete_like_prefix(directory)
            query = query.where(Entity.file_path.like(f"{escaped_directory}/%", escape="\\"))

        result = await session.execute(query.order_by(Entity.file_path.asc()))
        return [
            plan_directory_file_snapshot(
                entity_id=int(row.id),
                file_path=str(row.file_path),
                entity_checksum=str(row.checksum) if row.checksum is not None else None,
                entity_mtime=float(row.mtime) if row.mtime is not None else None,
                entity_size=int(row.size) if row.size is not None else None,
                note_file_checksum=(
                    str(row.note_file_checksum) if row.note_file_checksum is not None else None
                ),
                note_file_updated_at=row.note_file_updated_at,
            )
            for row in result.all()
        ]

    async def delete_directory_entities(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        project_external_id: ProjectExternalId,
        directory: RuntimeFilePath,
        entity_ids: Sequence[int],
    ) -> DirectoryEntityDeleteResult:
        if not entity_ids:
            return DirectoryEntityDeleteResult()
        snapshotted_entity_ids = tuple(sorted(set(entity_ids)))

        # The directory snapshot is intentionally a plain read. Claim every accepted
        # source here, immediately before the first operation that can lock Entity or
        # Relation rows. See current_relation_generation_statement for the canonical
        # NoteContent-first invariant shared with relation publication.
        await lock_note_content_before_entity_mutation(
            session,
            project_id=project_id,
            entity_ids=snapshotted_entity_ids,
        )

        membership_predicates = [
            Entity.id.in_(snapshotted_entity_ids),
            Entity.project_id == project_id,
        ]
        if directory not in {"", "/"}:
            escaped_directory = directory_delete_like_prefix(directory)
            membership_predicates.append(
                Entity.file_path.like(f"{escaped_directory}/%", escape="\\")
            )

        # The NoteContent fence is this flow's only lock construct. Re-read current
        # membership without locking so projection cleanup follows only rows still in
        # the directory, then repeat the predicate in DELETE so the stale snapshot
        # itself never authorizes mutation.
        current_directory_entities = await session.execute(
            select(Entity.id).where(*membership_predicates).order_by(Entity.id)
        )
        current_directory_entity_ids = tuple(
            int(entity_id) for entity_id in current_directory_entities.scalars()
        )
        if not current_directory_entity_ids:
            return DirectoryEntityDeleteResult()

        # A bulk delete must not bypass a note's lock or partially delete its siblings.
        # Read accepted Markdown under the existing mutation fence, before any deletes.
        # The same read keeps each note's last accepted revision for its journal row:
        # note_content cascades away with the entity, so it cannot be read afterwards.
        note_contents = (
            await session.execute(
                select(
                    NoteContent.entity_id,
                    NoteContent.markdown_content,
                    NoteContent.db_version,
                    NoteContent.db_checksum,
                ).where(NoteContent.entity_id.in_(current_directory_entity_ids))
            )
        ).all()
        if any(note_is_locked(row.markdown_content) for row in note_contents):
            raise DirectoryDeleteRejected(
                DirectoryDeleteRejection(
                    kind=DirectoryDeleteRejectKind.locked,
                    detail=LOCKED_NOTE_MESSAGE,
                )
            )

        # Capture surviving sources before the delete: Relation.to_id CASCADE will drop
        # the relation table rows for incoming links from entities outside the directory,
        # but those sources own the matching search_index relation rows and are never in
        # the deleted set, so the caller must reindex them to clear the stale rows.
        incoming_relations = await session.execute(
            select(Relation.id, Relation.to_id, Relation.from_id).where(
                Relation.project_id == project_id,
                Relation.to_id.in_(current_directory_entity_ids),
                Relation.from_id.not_in(current_directory_entity_ids),
            )
        )
        incoming_relation_snapshots_list: list[tuple[int, int, int]] = []
        for relation_id, target_id, source_id in incoming_relations.tuples().all():
            if target_id is None:  # pragma: no cover
                raise RuntimeError("Resolved incoming relation is missing its target id")
            incoming_relation_snapshots_list.append(
                (int(relation_id), int(target_id), int(source_id))
            )
        incoming_relation_snapshots = tuple(incoming_relation_snapshots_list)
        incoming_relation_ids = tuple(
            relation_id for relation_id, _, _ in incoming_relation_snapshots
        )

        uncaptured_incoming_relation = select(Relation.id).where(
            Relation.project_id == project_id,
            Relation.to_id == Entity.id,
            Relation.from_id.not_in(current_directory_entity_ids),
        )
        if incoming_relation_ids:
            uncaptured_incoming_relation = uncaptured_incoming_relation.where(
                Relation.id.not_in(incoming_relation_ids)
            )

        # Trigger: a resolver can publish an incoming edge after the unlocked capture.
        # Why: deleting its target would cascade that edge without scheduling its source
        # for search repair. The lock budget deliberately excludes target Entity locks.
        # Outcome: optimistic deletion rejects only targets with uncaptured incoming work;
        # a later directory pass can retry from a fresh snapshot.
        guarded_entity_delete = delete(Entity).where(
            *membership_predicates,
            ~exists(uncaptured_incoming_relation.correlate(Entity)),
        )
        deleted_entities = (
            await session.execute(
                guarded_entity_delete.returning(
                    Entity.id,
                    Entity.external_id,
                    Entity.permalink,
                    Entity.title,
                    Entity.file_path,
                    Entity.content_type,
                )
            )
        ).all()
        deleted_entity_ids = frozenset(int(row.id) for row in deleted_entities)
        if not deleted_entity_ids:
            return DirectoryEntityDeleteResult()
        ordered_deleted_entity_ids = tuple(sorted(deleted_entity_ids))

        relation_cleanup_entity_ids = frozenset(
            source_id
            for _, target_id, source_id in incoming_relation_snapshots
            if target_id in deleted_entity_ids
        )

        await session.execute(
            text(
                """
                DELETE FROM search_index
                WHERE project_id = :project_id AND entity_id IN :entity_ids
                """
            ).bindparams(bindparam("entity_ids", expanding=True)),
            {"project_id": project_id, "entity_ids": ordered_deleted_entity_ids},
        )
        if self.external_vector_cleaner is None:
            await delete_project_index_vector_rows(
                session,
                project_id=project_id,
                entity_ids=ordered_deleted_entity_ids,
            )
        else:
            await delete_project_index_vector_rows(
                session,
                project_id=project_id,
                entity_ids=ordered_deleted_entity_ids,
                external_vector_cleaner=self.external_vector_cleaner,
            )

        # Journal last: claiming positions locks the project row, so keep that lock
        # to the end of the transaction, as the single-note delete does.
        project_changes = await record_directory_delete_project_changes(
            session,
            project_id=project_id,
            project_external_id=project_external_id,
            deleted_entities=deleted_entities,
            note_revisions={
                int(row.entity_id): (row.db_version, row.db_checksum) for row in note_contents
            },
        )
        return DirectoryEntityDeleteResult(
            deleted_entity_ids=deleted_entity_ids,
            relation_cleanup_entity_ids=relation_cleanup_entity_ids,
            project_changes=project_changes,
        )


class DirectoryDeletedEntityRow(Protocol):
    """Entity columns returned by the guarded directory delete."""

    @property
    def id(self) -> int: ...

    @property
    def external_id(self) -> str: ...

    @property
    def permalink(self) -> str | None: ...

    @property
    def title(self) -> str: ...

    @property
    def file_path(self) -> str: ...

    @property
    def content_type(self) -> str: ...


async def record_directory_delete_project_changes(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    project_external_id: ProjectExternalId,
    deleted_entities: Sequence[DirectoryDeletedEntityRow],
    note_revisions: Mapping[int, tuple[int | None, str | None]],
    project_repository: ProjectRepository | None = None,
) -> dict[RuntimeEntityId, RuntimeAcceptedProjectNoteChange]:
    """Write one `deleted` journal row per removed Markdown note, in the caller's transaction.

    Non-Markdown files get no row, matching the single-note delete. Positions are
    claimed in one statement and assigned in file-path order, so the batch occupies
    consecutive positions and replays in a stable order.
    """
    markdown_entities: list[DirectoryDeletedEntityRow] = []
    for row in deleted_entities:
        if not runtime_content_type_is_markdown(row):
            continue
        # Trigger: a Markdown note with no permalink.
        # Why: a journal row needs a permalink, and every deleted Markdown note must
        #   get a row, or journal consumers keep treating that note as present.
        # Outcome: fail before claiming any position. The caller's transaction rolls
        #   back the whole folder delete, matching the single-note delete.
        if row.permalink is None:
            raise RuntimeError(f"Accepted note is missing permalink for entity_id={row.id}")
        markdown_entities.append(row)
    markdown_entities.sort(key=lambda row: (row.file_path, int(row.id)))
    if not markdown_entities:
        return {}

    repository = project_repository or ProjectRepository()
    head = await repository.advance_partition_position_by(
        session,
        project_id,
        len(markdown_entities),
    )
    first_position = head - len(markdown_entities) + 1
    accepted_at = datetime.now(tz=UTC)
    changes: dict[RuntimeEntityId, RuntimeAcceptedProjectNoteChange] = {}
    for offset, row in enumerate(markdown_entities):
        entity_id = int(row.id)
        db_version, db_checksum = note_revisions.get(entity_id, (None, None))
        changes[entity_id] = RuntimeAcceptedProjectNoteChange(
            project_id=project_id,
            project_external_id=project_external_id,
            partition_position=first_position + offset,
            entity_id=entity_id,
            note_external_id=str(row.external_id),
            permalink=str(row.permalink),
            title=str(row.title),
            operation=RuntimeProjectNoteOperation.deleted,
            file_path=str(row.file_path),
            previous_file_path=None,
            accepted_at=accepted_at,
            source=ACCEPTED_DIRECTORY_DELETE_SOURCE,
            db_version=int(db_version) if db_version is not None else None,
            db_checksum=str(db_checksum) if db_checksum is not None else None,
        )
    await repository.record_accepted_note_changes(session, tuple(changes.values()))
    return changes


def normalize_directory_delete_path(directory: str) -> RuntimeFilePath:
    """Normalize a project-relative directory delete path or reject traversal."""
    normalized_directory = directory.strip().strip("/")
    if not valid_project_path_value(normalized_directory):
        raise ValueError("Invalid directory path")
    return normalized_directory


def directory_delete_like_prefix(directory: RuntimeFilePath) -> RuntimeFilePath:
    """Escape one normalized directory path for SQL LIKE prefix matching."""
    return directory.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


@dataclass(frozen=True, slots=True)
class DirectoryDeleteFileFailure:
    """One accepted file left on disk after cleanup, with its caller-visible reason."""

    file_path: RuntimeFilePath
    reason: str


class DirectoryDeleteErrorPayload(TypedDict):
    """One failed file delete in the existing route error contract."""

    path: RuntimeFilePath
    error: str


class DirectoryDeleteResponsePayload(TypedDict):
    """Existing directory-delete route response contract.

    Extends the client-facing DirectoryDeleteResult schema with the runtime
    ``file_delete_status``/``error`` fields that queued cleanups report.
    """

    total_files: int
    successful_deletes: int
    failed_deletes: int
    deleted_files: list[RuntimeFilePath]
    errors: list[DirectoryDeleteErrorPayload]
    file_delete_status: DirectoryDeleteFileStatus
    error: NotRequired[str]


@dataclass(frozen=True, slots=True)
class DirectoryDeleteAcceptedResult:
    """Existing directory-delete response shape before route serialization."""

    deleted_files: tuple[RuntimeFilePath, ...]
    file_delete_status: DirectoryDeleteFileStatus
    error: str | None = None
    # Files whose guarded cleanup was skipped (checksum changed / no accepted
    # checksum) or whose cleanup enqueue failed; they remain on disk despite the
    # DB row being deleted. "missing" results count as successful (already gone).
    failed_files: tuple[DirectoryDeleteFileFailure, ...] = ()
    # Surviving source entities whose relations pointed into the deleted directory;
    # the caller reindexes these to clear now-stale relation rows from search.
    relation_cleanup_entity_ids: frozenset[int] = frozenset()
    # Journal rows no queued job will settle: the cleanup already ran inline (any
    # outcome) or could not be queued. The caller settles them; see
    # settle_directory_delete_project_changes.
    project_changes_to_settle: tuple[RuntimeAcceptedProjectNoteChange, ...] = ()

    @classmethod
    def complete(cls) -> "DirectoryDeleteAcceptedResult":
        """Return the response for a directory delete with no matching files."""
        return cls(deleted_files=(), file_delete_status="complete")

    @classmethod
    def pending(
        cls,
        *,
        deleted_files: Sequence[RuntimeFilePath],
        failed_files: Sequence[DirectoryDeleteFileFailure] = (),
        relation_cleanup_entity_ids: frozenset[int] = frozenset(),
        project_changes_to_settle: Sequence[RuntimeAcceptedProjectNoteChange] = (),
    ) -> "DirectoryDeleteAcceptedResult":
        """Return the response after DB rows were deleted and cleanup was queued."""
        return cls(
            deleted_files=tuple(deleted_files),
            file_delete_status="pending",
            failed_files=tuple(failed_files),
            relation_cleanup_entity_ids=relation_cleanup_entity_ids,
            project_changes_to_settle=tuple(project_changes_to_settle),
        )

    @classmethod
    def failed(
        cls,
        *,
        deleted_files: Sequence[RuntimeFilePath],
        error: str,
    ) -> "DirectoryDeleteAcceptedResult":
        """Return the response after DB rows were deleted but cleanup queueing failed."""
        return cls(
            deleted_files=tuple(deleted_files),
            file_delete_status="failed",
            error=error,
        )

    @property
    def http_status_code(self) -> int:
        """Return the route status for this result.

        A failed cleanup enqueue leaves files on disk with their DB rows gone,
        so the route reports a server error instead of a clean success.
        """
        return 500 if self.file_delete_status == "failed" else 200

    def to_response_payload(self) -> DirectoryDeleteResponsePayload:
        """Serialize to the current Basic Memory directory-delete response contract.

        A guarded cleanup that left files on disk (failed_files) is reported as
        failed_deletes with reasons, not folded into successful_deletes — otherwise
        callers see a clean success while stale files remain and later reappear.
        """
        failed = len(self.failed_files)
        payload: DirectoryDeleteResponsePayload = {
            "total_files": len(self.deleted_files),
            "successful_deletes": len(self.deleted_files) - failed,
            "failed_deletes": failed,
            "deleted_files": list(self.deleted_files),
            # The MCP/CLI client validates errors as DirectoryDeleteError objects
            # ({path, error}); plain strings fail Pydantic validation client-side
            # on every partial failure, so serialize structured entries.
            "errors": [
                {"path": failure.file_path, "error": failure.reason}
                for failure in self.failed_files
            ],
            "file_delete_status": self.file_delete_status,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


class DirectoryFileDeleteEnqueuer(Protocol):
    """Capability that queues cleanup for one accepted directory-delete file.

    Returns the guarded delete result when cleanup runs inline (local runtime),
    or None when the work is queued for later (cloud runtime, outcome deferred).
    """

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeFileDeleteResult | None: ...


@dataclass(frozen=True, slots=True)
class DirectoryFileDeleteEnqueueFailure:
    """One accepted file whose cleanup could not be queued or run inline."""

    file_path: RuntimeFilePath
    reason: str


@dataclass(frozen=True, slots=True)
class DirectoryFileDeleteEnqueueOutcome:
    """Aggregate of one directory delete's per-file cleanup enqueue attempts."""

    results: tuple[RuntimeFileDeleteResult, ...] = ()
    failures: tuple[DirectoryFileDeleteEnqueueFailure, ...] = ()
    # Journal rows of files whose cleanup ran inline or failed to enqueue: no
    # queued job exists to settle them.
    project_changes_to_settle: tuple[RuntimeAcceptedProjectNoteChange, ...] = ()


async def enqueue_directory_file_delete_jobs(
    *,
    project_id: ProjectId,
    files: Sequence[RuntimeDirectoryFileSnapshot],
    enqueuer: DirectoryFileDeleteEnqueuer,
    project_changes: DirectoryDeleteProjectChanges | None = None,
) -> DirectoryFileDeleteEnqueueOutcome:
    """Queue guarded cleanup jobs for files accepted by a directory delete.

    Returns the inline cleanup results (local runtime); deferred/queued cleanups
    (cloud runtime) contribute nothing here. Per-file enqueue failures are collected
    rather than raised so one unqueueable file does not abort cleanup of the rest.
    Each request carries its note's journal row, so a queued job can settle it.
    """
    changes_by_entity = project_changes or {}
    results: list[RuntimeFileDeleteResult] = []
    failures: list[DirectoryFileDeleteEnqueueFailure] = []
    changes_to_settle: list[RuntimeAcceptedProjectNoteChange] = []
    for file_snapshot in files:
        project_change = changes_by_entity.get(file_snapshot.entity_id)
        # Trigger: one file's guarded delete/enqueue raises (unreadable file, queue down).
        # Why: the entity row is already deleted, so aborting the batch strands every
        #   remaining file on disk with its DB row gone; sync then resurrects them.
        # Outcome: record the failure, keep going, and report it as a failed delete.
        try:
            result = await enqueuer.enqueue_directory_file_delete(
                plan_note_file_delete_job_request(
                    file_snapshot.to_pending_note_file_delete(
                        project_id=project_id,
                        project_change=project_change,
                    )
                )
            )
        except DirectoryFileDeleteEnqueueError as exc:
            failures.append(
                DirectoryFileDeleteEnqueueFailure(
                    file_path=file_snapshot.file_path,
                    reason=str(exc),
                )
            )
            if project_change is not None:
                changes_to_settle.append(project_change)
            continue
        if result is not None:
            results.append(result)
            if project_change is not None:
                changes_to_settle.append(project_change)
    return DirectoryFileDeleteEnqueueOutcome(
        results=tuple(results),
        failures=tuple(failures),
        project_changes_to_settle=tuple(changes_to_settle),
    )


async def settle_directory_delete_project_changes(
    session: AsyncSession,
    result: DirectoryDeleteAcceptedResult,
    *,
    project_repository: ProjectRepository | None = None,
) -> None:
    """Mark the journal rows that no queued cleanup job will settle as materialized.

    The wiki projector defers while any replayed row is unsettled (GitHub #1625).
    The entity delete is final whatever happened to the file: a file left on disk
    by a skipped or failed cleanup is re-indexed later as a new note. Waiting on it
    would stall projection for good, so every outcome settles.
    """
    changes = result.project_changes_to_settle
    if not changes:
        return
    project_ids = {change.project_id for change in changes}
    if len(project_ids) != 1:  # pragma: no cover
        raise RuntimeError("Directory delete journal rows must belong to one project")
    await (project_repository or ProjectRepository()).mark_accepted_note_changes_materialized(
        session,
        project_ids.pop(),
        [change.partition_position for change in changes],
        materialized_at=datetime.now(tz=UTC),
    )


async def accept_directory_delete(
    session: AsyncSession,
    *,
    request: DirectoryDeleteAcceptanceRequest,
    store: DirectoryDeleteAcceptanceStore,
    check_file_locks: DirectoryFileLockCheck | None = None,
) -> DirectoryDeleteAcceptance:
    """Accept a directory delete into DB state before post-commit cleanup jobs."""
    try:
        directory = normalize_directory_delete_path(request.directory)
    except ValueError as exc:
        raise DirectoryDeleteRejected(
            DirectoryDeleteRejection(
                kind=DirectoryDeleteRejectKind.bad_request,
                detail="Invalid directory path",
            )
        ) from exc

    project_id = await store.load_project_id(session, request.project_external_id)
    if project_id is None:
        raise DirectoryDeleteRejected(
            DirectoryDeleteRejection(
                kind=DirectoryDeleteRejectKind.not_found,
                detail=f"Project '{request.project_external_id}' not found",
            )
        )

    file_snapshots = tuple(
        await store.load_directory_file_snapshots(
            session,
            project_id=project_id,
            directory=directory,
        )
    )
    if not file_snapshots:
        return DirectoryDeleteAcceptance(project_id=project_id, files=())

    # Local files may have acquired a lock since the last index pass. Check them
    # before accepting any deletion; hosted runtimes use accepted Markdown below.
    if check_file_locks is not None:
        await check_file_locks(file_snapshots)

    delete_result = await store.delete_directory_entities(
        session,
        project_id=project_id,
        project_external_id=request.project_external_id,
        directory=directory,
        entity_ids=[snapshot.entity_id for snapshot in file_snapshots],
    )
    deleted_file_snapshots = tuple(
        snapshot
        for snapshot in file_snapshots
        if snapshot.entity_id in delete_result.deleted_entity_ids
    )
    return DirectoryDeleteAcceptance(
        project_id=project_id,
        files=deleted_file_snapshots,
        relation_cleanup_entity_ids=delete_result.relation_cleanup_entity_ids,
        project_changes=delete_result.project_changes,
    )


async def finish_directory_delete_acceptance(
    *,
    request: DirectoryDeleteAcceptanceRequest,
    accepted: DirectoryDeleteAcceptance,
    enqueuer: DirectoryFileDeleteEnqueuer,
) -> DirectoryDeleteAcceptedResult:
    """Queue cleanup for an accepted directory delete after the DB commit."""
    if not accepted.files:
        return DirectoryDeleteAcceptedResult.complete()

    outcome = await enqueue_directory_file_delete_jobs(
        project_id=accepted.project_id,
        files=accepted.files,
        enqueuer=enqueuer,
        project_changes=accepted.project_changes,
    )

    # Both a guarded skip (checksum changed) and an enqueue failure leave the file on
    # disk while its DB row is gone; surface both as failed deletes instead of reporting
    # a clean success that later reappears when sync re-indexes the stranded file.
    skipped = [r for r in outcome.results if r.status == RuntimeDeleteStatus.skipped]
    failed_files = tuple(
        DirectoryDeleteFileFailure(file_path=result.file_path, reason=result.reason)
        for result in skipped
    ) + tuple(
        DirectoryDeleteFileFailure(file_path=failure.file_path, reason=failure.reason)
        for failure in outcome.failures
    )
    return DirectoryDeleteAcceptedResult.pending(
        deleted_files=accepted.deleted_files,
        failed_files=failed_files,
        relation_cleanup_entity_ids=accepted.relation_cleanup_entity_ids,
        project_changes_to_settle=outcome.project_changes_to_settle,
    )


async def run_directory_delete(
    session: AsyncSession,
    *,
    request: DirectoryDeleteAcceptanceRequest,
    runtime: DirectoryDeleteRuntime,
) -> DirectoryDeleteAcceptedResult:
    """Accept a directory delete into DB state and queue guarded file cleanup."""
    accepted = await accept_directory_delete(
        session,
        request=request,
        store=runtime.store,
        check_file_locks=runtime.check_file_locks,
    )
    result = await finish_directory_delete_acceptance(
        request=request,
        accepted=accepted,
        enqueuer=runtime.file_delete_enqueuer,
    )
    await settle_directory_delete_project_changes(session, result)
    return result
