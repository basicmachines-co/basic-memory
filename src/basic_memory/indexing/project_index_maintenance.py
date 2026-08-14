"""Project-index move/delete maintenance for indexed project state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import override, Protocol

from loguru import logger
from sqlalchemy import RowMapping, bindparam, case, column, delete, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, NoteContent, Relation
from basic_memory.repository.accepted_note_vector_cleanup import (
    ProjectIndexExternalVectorCleaner,
    delete_project_index_vector_rows,
)
from basic_memory.repository.relation_repository import (
    lock_note_content_before_entity_mutation,
)
from basic_memory.read_cache import ReadCacheInvalidator, invalidate_cache
from basic_memory.runtime.storage import ProjectExternalId, ProjectId


class ProjectIndexMaintenanceRunner(Protocol):
    """Capability that applies project-wide move/delete maintenance."""

    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
    ) -> ProjectIndexMoveRun: ...

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
    ) -> ProjectIndexDeleteRun: ...


class ProjectIndexMoveBatchStore(Protocol):
    """Capability that commits one project-index move batch."""

    async def apply_project_index_move_batch(
        self,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult: ...


class ProjectIndexDeleteBatchStore(Protocol):
    """Capability that commits one project-index delete batch."""

    async def apply_project_index_delete_batch(
        self,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult: ...


class ProjectIndexMovedEntityRepository(Protocol):
    """Repository capability for loading moved entities after path maintenance."""

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> Sequence[Entity]:
        """Return moved entities by database id."""


class ProjectIndexMovedEntityIndexer(Protocol):
    """Search capability for refreshing one moved entity."""

    async def index_entity(self, entity: Entity) -> object:
        """Refresh search rows for one entity."""


class ProjectIndexMovedEntitySearchRefresher(Protocol):
    """Capability that repairs search rows for moved entities."""

    async def refresh_moved_entities(self, entity_ids: Sequence[int]) -> None:
        """Refresh search rows for moved entity ids."""


class ProjectIndexDeletePathVerifier(Protocol):
    """Capability that re-confirms scan-planned delete paths at apply time.

    Delete plans come from a storage snapshot that is stale by the time the
    batch applies; a note accepted and materialized after the snapshot would
    otherwise be destroyed. Implementations return only the paths whose
    absence they can positively confirm right now.
    """

    async def confirm_deleted_paths(self, paths: Sequence[str]) -> frozenset[str]:
        """Return the subset of paths confirmed absent from storage."""
        ...


@dataclass(frozen=True, slots=True)
class TrustPlannedProjectIndexDeleteVerifier(ProjectIndexDeletePathVerifier):
    """Confirm deletes already proven by a path-specific storage event.

    This verifier is not safe for project scans: their listing is a stale
    snapshot by the time a delete batch applies. Scan runtimes must inject a
    verifier that probes each candidate's current storage state.
    """

    @override
    async def confirm_deleted_paths(self, paths: Sequence[str]) -> frozenset[str]:
        return frozenset(paths)


@dataclass(frozen=True, slots=True)
class ProjectIndexDeleteEntity:
    """Stable entity identity carried through one delete eligibility check."""

    entity_id: int
    file_path: str


@dataclass(frozen=True, slots=True)
class StorageOwnedProjectIndexDeleteCandidate:
    """An indexed file with no DB-accepted Markdown content."""

    entity: ProjectIndexDeleteEntity


@dataclass(frozen=True, slots=True)
class MaterializedNoteProjectIndexDeleteCandidate:
    """A note whose accepted content is fully represented by its file."""

    entity: ProjectIndexDeleteEntity
    db_version: int
    db_checksum: str
    file_version: int
    file_checksum: str
    last_materialization_attempt_at: datetime | None


type ProjectIndexDeleteCandidate = (
    StorageOwnedProjectIndexDeleteCandidate | MaterializedNoteProjectIndexDeleteCandidate
)


@dataclass(frozen=True, slots=True)
class ProjectIndexDeleteCandidateScreen:
    """Candidate rows plus paths that exist but are not currently deletable."""

    candidates: tuple[ProjectIndexDeleteCandidate, ...]
    indexed_paths: frozenset[str]


class ProjectIndexDeleteCandidateRow(Protocol):
    """Persistence projection consumed by delete-candidate classification."""

    def __getitem__(self, key: str, /) -> object: ...


def _project_index_delete_candidate_from_row(
    row: ProjectIndexDeleteCandidateRow,
) -> ProjectIndexDeleteCandidate | None:
    """Classify one persistence row into the positive delete-eligible domain."""
    entity_id = row["entity_id"]
    file_path = row["file_path"]
    if not isinstance(entity_id, int) or not isinstance(file_path, str):
        raise TypeError("delete candidate identity must contain an integer id and string path")
    entity = ProjectIndexDeleteEntity(
        entity_id=entity_id,
        file_path=file_path,
    )
    if row["note_content_entity_id"] is None:
        return StorageOwnedProjectIndexDeleteCandidate(entity=entity)

    db_version = row["db_version"]
    db_checksum = row["db_checksum"]
    file_version = row["file_version"]
    file_checksum = row["file_checksum"]
    if row["file_write_status"] != "synced":
        return None
    if (
        not isinstance(db_version, int)
        or not isinstance(db_checksum, str)
        or not isinstance(file_version, int)
        or not isinstance(file_checksum, str)
    ):
        return None
    if file_version != db_version or file_checksum != db_checksum:
        return None

    attempted_at = row["last_materialization_attempt_at"]
    if attempted_at is not None and not isinstance(attempted_at, datetime):
        raise TypeError("last_materialization_attempt_at must be a datetime")
    return MaterializedNoteProjectIndexDeleteCandidate(
        entity=entity,
        db_version=db_version,
        db_checksum=db_checksum,
        file_version=file_version,
        file_checksum=file_checksum,
        last_materialization_attempt_at=attempted_at,
    )


async def _load_project_index_delete_candidates(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    paths: Sequence[str],
) -> ProjectIndexDeleteCandidateScreen:
    """Load the exact lineage that permits deletion for the requested paths."""
    if not paths:
        return ProjectIndexDeleteCandidateScreen(candidates=(), indexed_paths=frozenset())

    result = await session.execute(
        select(
            Entity.id.label("entity_id"),
            Entity.file_path.label("file_path"),
            NoteContent.entity_id.label("note_content_entity_id"),
            NoteContent.db_version.label("db_version"),
            NoteContent.db_checksum.label("db_checksum"),
            NoteContent.file_version.label("file_version"),
            NoteContent.file_checksum.label("file_checksum"),
            NoteContent.file_write_status.label("file_write_status"),
            NoteContent.last_materialization_attempt_at.label("last_materialization_attempt_at"),
        )
        .outerjoin(NoteContent, NoteContent.entity_id == Entity.id)
        .where(
            Entity.project_id == project_id,
            Entity.file_path.in_(tuple(paths)),
        )
        .order_by(Entity.id)
    )
    rows = result.mappings().all()
    candidates = tuple(
        candidate
        for row in rows
        if (candidate := _project_index_delete_candidate_from_row(row)) is not None
    )
    return ProjectIndexDeleteCandidateScreen(
        candidates=candidates,
        indexed_paths=frozenset(str(row["file_path"]) for row in rows),
    )


@dataclass(frozen=True, slots=True)
class ProjectIndexMovedFile:
    """One indexed file move that may need storage-backed metadata repair."""

    entity_id: int
    old_path: str
    new_path: str
    old_permalink: str | None


@dataclass(frozen=True, slots=True)
class ProjectIndexMovedFileContentUpdate:
    """Planned markdown metadata rewrite for a moved file.

    ``checksum`` is computed from ``markdown_content`` — the exact bytes the
    post-commit write persists — so the database rows stamped during the batch
    transaction agree with the file once the write lands.
    """

    permalink: str
    checksum: str
    markdown_content: str


class ProjectIndexMoveContentUpdater(Protocol):
    """Capability that plans and persists provider-specific moved-file content repair.

    Planning runs inside the move batch's database transaction and must not
    mutate storage: the batch can still roll back (e.g. an intra-batch
    permalink collision), and an already-rewritten file would survive that
    rollback. The write runs only after the batch commits.
    """

    async def plan_moved_file_content(
        self,
        session: AsyncSession,
        moved_file: ProjectIndexMovedFile,
    ) -> ProjectIndexMovedFileContentUpdate | None: ...

    async def write_moved_file_content(
        self,
        moved_file: ProjectIndexMovedFile,
        content_update: ProjectIndexMovedFileContentUpdate,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectIndexMoveTarget:
    """One persisted file-path move for project-index maintenance."""

    old_path: str
    new_path: str


@dataclass(frozen=True, slots=True)
class ProjectIndexMoveBatch:
    """A bounded group of move targets for one database update."""

    completed_batches: int
    targets: tuple[ProjectIndexMoveTarget, ...]


@dataclass(frozen=True, slots=True)
class ProjectIndexMoveBatchPlan:
    """Portable move-maintenance work for a project-index run."""

    total_moves: int
    batch_count: int
    batches: tuple[ProjectIndexMoveBatch, ...]


@dataclass(frozen=True, slots=True)
class ProjectIndexMoveBatchProgress:
    """Existing workflow progress payload for completed move batches."""

    moved_files: int
    completed_batches: int
    total_batches: int
    updated_files: int

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize to the existing cloud workflow progress metadata shape."""
        return {
            "moved_files": self.moved_files,
            "completed_batches": self.completed_batches,
            "total_batches": self.total_batches,
            "updated_files": self.updated_files,
        }


@dataclass(frozen=True, slots=True)
class ProjectIndexMoveBatchResult:
    """Storage adapter result for one project-index move batch."""

    updated_files: int
    moved_entity_ids: frozenset[int] = frozenset()
    replaced_entity_ids: frozenset[int] = frozenset()
    relation_cleanup_entity_ids: frozenset[int] = frozenset()
    missing_paths: tuple[str, ...] = ()
    dropped_move_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectIndexMoveBatchRecord:
    """Observed result and progress metadata for one move batch."""

    batch: ProjectIndexMoveBatch
    result: ProjectIndexMoveBatchResult
    progress: ProjectIndexMoveBatchProgress


@dataclass(frozen=True, slots=True)
class ProjectIndexMoveRun:
    """Summary of a complete move-maintenance run."""

    total_moves: int
    total_updated_files: int
    records: tuple[ProjectIndexMoveBatchRecord, ...]
    moved_entity_ids: frozenset[int] = frozenset()
    replaced_entity_ids: frozenset[int] = frozenset()
    relation_cleanup_entity_ids: frozenset[int] = frozenset()

    @property
    def missing_paths(self) -> tuple[str, ...]:
        """Return every move source path that the runtime could not update."""
        return tuple(
            missing_path for record in self.records for missing_path in record.result.missing_paths
        )

    @property
    def dropped_move_paths(self) -> tuple[str, ...]:
        """Return every move source path dropped because its destination changed."""
        return tuple(
            dropped_path
            for record in self.records
            for dropped_path in record.result.dropped_move_paths
        )


@dataclass(frozen=True, slots=True)
class ProjectIndexDeleteBatch:
    """A bounded group of deleted paths for one database delete pass."""

    completed_batches: int
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectIndexDeleteBatchPlan:
    """Portable delete-maintenance work for a project-index run."""

    total_deletes: int
    batch_count: int
    batches: tuple[ProjectIndexDeleteBatch, ...]


@dataclass(frozen=True, slots=True)
class ProjectIndexDeleteBatchProgress:
    """Existing workflow progress payload for completed delete batches."""

    deleted_files: int
    completed_batches: int
    total_batches: int
    deleted_entities: int

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize to the existing cloud workflow progress metadata shape."""
        return {
            "deleted_files": self.deleted_files,
            "completed_batches": self.completed_batches,
            "total_batches": self.total_batches,
            "deleted_entities": self.deleted_entities,
        }


@dataclass(frozen=True, slots=True)
class ProjectIndexDeleteBatchResult:
    """Storage adapter result for one project-index delete batch."""

    deleted_entities: int
    relation_cleanup_entity_ids: frozenset[int] = frozenset()
    missing_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectIndexDeleteBatchRecord:
    """Observed result and progress metadata for one delete batch."""

    batch: ProjectIndexDeleteBatch
    result: ProjectIndexDeleteBatchResult
    progress: ProjectIndexDeleteBatchProgress | None


@dataclass(frozen=True, slots=True)
class ProjectIndexDeleteRun:
    """Summary of a complete delete-maintenance run."""

    total_deletes: int
    total_deleted_entities: int
    relation_cleanup_entity_ids: frozenset[int]
    records: tuple[ProjectIndexDeleteBatchRecord, ...]

    @property
    def missing_paths(self) -> tuple[str, ...]:
        """Return every deleted path that the runtime could not find."""
        return tuple(
            missing_path for record in self.records for missing_path in record.result.missing_paths
        )

    @property
    def skipped_paths(self) -> tuple[str, ...]:
        """Return every planned delete path skipped because it is present again."""
        return tuple(
            skipped_path for record in self.records for skipped_path in record.result.skipped_paths
        )


DELETE_PROJECT_INDEX_SEARCH_ROWS_SQL = text("""
    DELETE FROM search_index
    WHERE project_id = :project_id
      AND (
            entity_id IN :deleted_entity_ids
            OR (
                type = :relation_row_type
                AND (
                    from_id IN :deleted_entity_ids
                    OR to_id IN :deleted_entity_ids
                )
            )
      )
""").bindparams(bindparam("deleted_entity_ids", expanding=True))

PROJECT_INDEX_SEARCH_INDEX_TABLE = table(
    "search_index",
    column("project_id"),
    column("entity_id"),
    column("type"),
    column("file_path"),
    column("permalink"),
)


async def delete_project_index_entities(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_ids: Sequence[int],
    external_vector_cleaner: ProjectIndexExternalVectorCleaner | None = None,
) -> frozenset[int]:
    """Delete indexed entities and return surviving relation sources needing repair."""
    deleted_entity_ids = tuple(sorted(set(entity_ids)))
    if not deleted_entity_ids:
        return frozenset()

    # See current_relation_generation_statement for the canonical lock order.
    # Entity deletion can cascade into Relation, so accepted sources are claimed
    # in sorted order before either table can be locked by this transaction.
    await lock_note_content_before_entity_mutation(
        session,
        project_id=project_id,
        entity_ids=deleted_entity_ids,
    )

    surviving_relation_sources = await session.execute(
        select(Relation.from_id)
        .where(
            Relation.project_id == project_id,
            Relation.to_id.in_(deleted_entity_ids),
            Relation.from_id.not_in(deleted_entity_ids),
        )
        .distinct()
    )
    relation_cleanup_entity_ids = frozenset(
        int(entity_id) for entity_id in surviving_relation_sources.scalars()
    )

    delete_params = {
        "project_id": project_id,
        "deleted_entity_ids": deleted_entity_ids,
        "relation_row_type": "relation",
    }
    await session.execute(DELETE_PROJECT_INDEX_SEARCH_ROWS_SQL, delete_params)
    if external_vector_cleaner is None:
        await delete_project_index_vector_rows(
            session,
            project_id=project_id,
            entity_ids=deleted_entity_ids,
        )
    else:
        await delete_project_index_vector_rows(
            session,
            project_id=project_id,
            entity_ids=deleted_entity_ids,
            external_vector_cleaner=external_vector_cleaner,
        )
    await session.execute(
        delete(Entity).where(
            Entity.project_id == project_id,
            Entity.id.in_(deleted_entity_ids),
        )
    )
    return relation_cleanup_entity_ids


@dataclass(frozen=True, slots=True)
class _MoveReplacementScreen:
    """Move-batch rows that survive destination verification."""

    target_rows: list[RowMapping]
    replacement_rows: list[RowMapping]
    dropped_move_paths: tuple[str, ...]


def _screen_replaced_move_targets(
    *,
    target_rows: list[RowMapping],
    replacement_rows: list[RowMapping],
    target_paths_by_old_path: dict[str, str],
) -> _MoveReplacementScreen:
    """Drop planned moves whose destination row indexes different content.

    The move was planned by matching the destination file's checksum to the
    source entity's indexed checksum, so that checksum is the only content a
    replacement row may legitimately index. A mismatch means the destination
    holds a concurrently created entity (e.g. an accepted-but-unmaterialized
    note); deleting it would destroy that entity, so the move is dropped for
    the next scan to reconcile.
    """
    old_path_by_new_path = {
        target_paths_by_old_path[str(row["file_path"])]: str(row["file_path"])
        for row in target_rows
    }
    expected_checksum_by_new_path = {
        target_paths_by_old_path[str(row["file_path"])]: row["checksum"] for row in target_rows
    }
    verified_replacement_rows: list[RowMapping] = []
    dropped_new_paths: set[str] = set()
    for replacement_row in replacement_rows:
        replacement_path = str(replacement_row["file_path"])
        expected_checksum = expected_checksum_by_new_path.get(replacement_path)
        if expected_checksum is not None and replacement_row["checksum"] == expected_checksum:
            verified_replacement_rows.append(replacement_row)
            continue
        dropped_new_paths.add(replacement_path)
        logger.warning(
            "Dropping planned move: destination holds a concurrently created entity",
            old_path=old_path_by_new_path.get(replacement_path),
            new_path=replacement_path,
        )

    if not dropped_new_paths:
        return _MoveReplacementScreen(
            target_rows=target_rows,
            replacement_rows=verified_replacement_rows,
            dropped_move_paths=(),
        )
    return _MoveReplacementScreen(
        target_rows=[
            row
            for row in target_rows
            if target_paths_by_old_path[str(row["file_path"])] not in dropped_new_paths
        ],
        replacement_rows=verified_replacement_rows,
        dropped_move_paths=tuple(
            sorted(old_path_by_new_path[new_path] for new_path in dropped_new_paths)
        ),
    )


@dataclass(frozen=True, slots=True)
class _MoveContentPlan:
    """Planned frontmatter rewrites for one move batch, keyed by entity id."""

    updates_by_entity_id: dict[int, ProjectIndexMovedFileContentUpdate]
    moved_files_by_entity_id: dict[int, ProjectIndexMovedFile]

    @classmethod
    def empty(cls) -> "_MoveContentPlan":
        return cls(updates_by_entity_id={}, moved_files_by_entity_id={})


@dataclass(frozen=True, slots=True)
class _MoveBatchUpdateValues:
    """Parallel per-table CASE assignments for one move batch."""

    entity_values: dict[str, object]
    note_content_values: dict[str, object]
    search_index_values: dict[str, object]
    permalinks_by_entity_id: dict[int, str]


def _build_move_batch_update_values(
    *,
    target_paths_by_old_path: dict[str, str],
    target_paths_by_entity_id: dict[int, str],
    content_updates_by_entity_id: dict[int, ProjectIndexMovedFileContentUpdate],
) -> _MoveBatchUpdateValues:
    """Assemble the parallel CASE assignments for entity/note_content/search_index.

    Every table repoints file_path in one statement; when content repair was
    planned, the checksum/permalink/markdown columns join those same statements
    so the batch transaction stamps rows that agree with the post-commit file
    writes.
    """
    entity_values: dict[str, object] = {
        "file_path": case(target_paths_by_old_path, value=Entity.file_path)
    }
    note_content_values: dict[str, object] = {
        "file_path": case(target_paths_by_entity_id, value=NoteContent.entity_id)
    }
    search_index_values: dict[str, object] = {
        "file_path": case(
            target_paths_by_entity_id,
            value=PROJECT_INDEX_SEARCH_INDEX_TABLE.c.entity_id,
        )
    }
    permalinks_by_entity_id: dict[int, str] = {}
    if content_updates_by_entity_id:
        checksums_by_entity_id = {
            entity_id: content_update.checksum
            for entity_id, content_update in content_updates_by_entity_id.items()
        }
        markdown_by_entity_id = {
            entity_id: content_update.markdown_content
            for entity_id, content_update in content_updates_by_entity_id.items()
        }
        permalinks_by_entity_id = {
            entity_id: content_update.permalink
            for entity_id, content_update in content_updates_by_entity_id.items()
        }
        entity_values["checksum"] = case(
            checksums_by_entity_id,
            value=Entity.id,
            else_=Entity.checksum,
        )
        entity_values["permalink"] = case(
            permalinks_by_entity_id,
            value=Entity.id,
            else_=Entity.permalink,
        )
        note_content_values["db_checksum"] = case(
            checksums_by_entity_id,
            value=NoteContent.entity_id,
            else_=NoteContent.db_checksum,
        )
        note_content_values["file_checksum"] = case(
            checksums_by_entity_id,
            value=NoteContent.entity_id,
            else_=NoteContent.file_checksum,
        )
        note_content_values["markdown_content"] = case(
            markdown_by_entity_id,
            value=NoteContent.entity_id,
            else_=NoteContent.markdown_content,
        )

    return _MoveBatchUpdateValues(
        entity_values=entity_values,
        note_content_values=note_content_values,
        search_index_values=search_index_values,
        permalinks_by_entity_id=permalinks_by_entity_id,
    )


@dataclass(frozen=True, slots=True)
class RepositoryProjectIndexMaintenanceStore:
    """Apply project-index move/delete maintenance with explicit sessions."""

    session_maker: async_sessionmaker[AsyncSession]
    project_id: ProjectId
    external_vector_cleaner: ProjectIndexExternalVectorCleaner | None = None
    move_content_updater: ProjectIndexMoveContentUpdater | None = None
    delete_path_verifier: ProjectIndexDeletePathVerifier | None = None
    # Trigger: an entity occupies a move destination at apply time.
    # Why: scan change planning only pairs moves with paths that had no DB row
    #      at snapshot time, so a row found there was created concurrently and
    #      may carry accepted-but-unmaterialized content; the watcher flow, by
    #      contrast, legitimately moves onto an existing indexed file and must
    #      keep replacing it unconditionally.
    # Outcome: scan runtimes set this True so a replacement is only deleted
    #          when its checksum proves it indexes the moved bytes; mismatches
    #          drop the move for the next scan to reconcile.
    verify_replaced_move_targets: bool = False

    async def apply_project_index_move_batch(
        self,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult:
        if not move_batch.targets:
            return ProjectIndexMoveBatchResult(updated_files=0)

        target_paths_by_old_path = {
            move_target.old_path: move_target.new_path for move_target in move_batch.targets
        }

        async with db.scoped_session(self.session_maker) as session:
            # --- Load the indexed rows the batch may rewrite ---
            existing_paths_result = await session.execute(
                select(Entity.id, Entity.file_path, Entity.permalink, Entity.checksum).where(
                    Entity.project_id == self.project_id,
                    Entity.file_path.in_(tuple(target_paths_by_old_path)),
                )
            )
            target_rows = list(existing_paths_result.mappings().all())
            replacement_rows = await self._load_move_replacement_rows(
                session,
                target_rows=target_rows,
                target_paths_by_old_path=target_paths_by_old_path,
            )

            # --- Screen destinations recreated concurrently ---
            # See verify_replaced_move_targets above: only scan runtimes verify,
            # and only when a row already occupies a destination path.
            dropped_move_paths: tuple[str, ...] = ()
            if self.verify_replaced_move_targets and replacement_rows:
                replacement_screen = _screen_replaced_move_targets(
                    target_rows=target_rows,
                    replacement_rows=replacement_rows,
                    target_paths_by_old_path=target_paths_by_old_path,
                )
                target_rows = replacement_screen.target_rows
                replacement_rows = replacement_screen.replacement_rows
                dropped_move_paths = replacement_screen.dropped_move_paths

            # --- Plan provider-specific content repair inside the transaction ---
            updated_old_paths = frozenset(str(row["file_path"]) for row in target_rows)
            target_paths_by_entity_id = {
                int(row["id"]): target_paths_by_old_path[str(row["file_path"])]
                for row in target_rows
            }
            replaced_entity_ids = frozenset(int(row["id"]) for row in replacement_rows)
            # A move updates NoteContent and Entity while also deleting any replaced
            # destination entities. Claim the complete union once, in canonical order,
            # before content planning or any mutation can acquire a later lock.
            await lock_note_content_before_entity_mutation(
                session,
                project_id=self.project_id,
                entity_ids=(*target_paths_by_entity_id, *replaced_entity_ids),
            )
            content_plan = await self._plan_move_content_updates(
                session,
                target_rows=target_rows,
                target_paths_by_old_path=target_paths_by_old_path,
            )

            # --- Apply the batched replacement deletes and path/content updates ---
            relation_cleanup_entity_ids: frozenset[int] = frozenset()
            if updated_old_paths:
                relation_cleanup_entity_ids = await delete_project_index_entities(
                    session,
                    project_id=self.project_id,
                    entity_ids=tuple(replaced_entity_ids),
                    external_vector_cleaner=self.external_vector_cleaner,
                )
                await self._execute_move_batch_updates(
                    session,
                    updated_old_paths=updated_old_paths,
                    target_paths_by_entity_id=target_paths_by_entity_id,
                    update_values=_build_move_batch_update_values(
                        target_paths_by_old_path=target_paths_by_old_path,
                        target_paths_by_entity_id=target_paths_by_entity_id,
                        content_updates_by_entity_id=content_plan.updates_by_entity_id,
                    ),
                )

        # --- Write planned file content after the commit ---
        await self._write_moved_file_contents(content_plan)

        # --- Report per-path outcomes ---
        missing_paths = tuple(
            move_target.old_path
            for move_target in move_batch.targets
            if move_target.old_path not in updated_old_paths
            and move_target.old_path not in dropped_move_paths
        )
        return ProjectIndexMoveBatchResult(
            updated_files=len(updated_old_paths),
            moved_entity_ids=frozenset(target_paths_by_entity_id),
            replaced_entity_ids=replaced_entity_ids,
            relation_cleanup_entity_ids=relation_cleanup_entity_ids,
            missing_paths=missing_paths,
            dropped_move_paths=dropped_move_paths,
        )

    async def _load_move_replacement_rows(
        self,
        session: AsyncSession,
        *,
        target_rows: list[RowMapping],
        target_paths_by_old_path: dict[str, str],
    ) -> list[RowMapping]:
        """Load entities already occupying the batch's move destinations.

        Rows can appear there when the watcher legitimately moves onto an
        existing indexed file, or when a racing event index created the moved
        file at its new path first; survivors are deleted so the source entity
        can take over the path.
        """
        if not target_rows:
            return []
        new_paths = tuple(
            sorted({target_paths_by_old_path[str(row["file_path"])] for row in target_rows})
        )
        replacement_result = await session.execute(
            select(Entity.id, Entity.file_path, Entity.checksum).where(
                Entity.project_id == self.project_id,
                Entity.file_path.in_(new_paths),
                Entity.id.not_in(tuple(int(row["id"]) for row in target_rows)),
            )
        )
        return list(replacement_result.mappings().all())

    async def _plan_move_content_updates(
        self,
        session: AsyncSession,
        *,
        target_rows: list[RowMapping],
        target_paths_by_old_path: dict[str, str],
    ) -> _MoveContentPlan:
        """Plan provider-specific frontmatter rewrites inside the batch transaction.

        Planning must not mutate storage: the batch can still roll back, and an
        already-rewritten file would survive that rollback (see
        ProjectIndexMoveContentUpdater). Runtimes without a content updater skip
        content repair entirely.
        """
        if self.move_content_updater is None:
            return _MoveContentPlan.empty()

        updates_by_entity_id: dict[int, ProjectIndexMovedFileContentUpdate] = {}
        moved_files_by_entity_id: dict[int, ProjectIndexMovedFile] = {}
        for row in target_rows:
            entity_id = int(row["id"])
            old_path = str(row["file_path"])
            moved_file = ProjectIndexMovedFile(
                entity_id=entity_id,
                old_path=old_path,
                new_path=target_paths_by_old_path[old_path],
                old_permalink=(str(row["permalink"]) if row["permalink"] is not None else None),
            )
            content_update = await self.move_content_updater.plan_moved_file_content(
                session,
                moved_file,
            )
            if content_update is not None:
                updates_by_entity_id[entity_id] = content_update
                moved_files_by_entity_id[entity_id] = moved_file
        return _MoveContentPlan(
            updates_by_entity_id=updates_by_entity_id,
            moved_files_by_entity_id=moved_files_by_entity_id,
        )

    async def _execute_move_batch_updates(
        self,
        session: AsyncSession,
        *,
        updated_old_paths: frozenset[str],
        target_paths_by_entity_id: dict[int, str],
        update_values: _MoveBatchUpdateValues,
    ) -> None:
        """Run the batched UPDATE statements for one screened set of moves."""
        await session.execute(
            update(Entity)
            .where(
                Entity.project_id == self.project_id,
                Entity.file_path.in_(updated_old_paths),
            )
            .values(**update_values.entity_values)
        )
        await session.execute(
            update(NoteContent)
            .where(
                NoteContent.project_id == self.project_id,
                NoteContent.entity_id.in_(tuple(target_paths_by_entity_id)),
            )
            .values(**update_values.note_content_values)
        )
        await session.execute(
            update(PROJECT_INDEX_SEARCH_INDEX_TABLE)
            .where(
                PROJECT_INDEX_SEARCH_INDEX_TABLE.c.project_id == self.project_id,
                PROJECT_INDEX_SEARCH_INDEX_TABLE.c.entity_id.in_(tuple(target_paths_by_entity_id)),
            )
            .values(**update_values.search_index_values)
        )
        # Entity search rows carry a permalink column that only changes when
        # content repair rewrote the note's permalink frontmatter.
        if update_values.permalinks_by_entity_id:
            await session.execute(
                update(PROJECT_INDEX_SEARCH_INDEX_TABLE)
                .where(
                    PROJECT_INDEX_SEARCH_INDEX_TABLE.c.project_id == self.project_id,
                    PROJECT_INDEX_SEARCH_INDEX_TABLE.c.entity_id.in_(
                        tuple(update_values.permalinks_by_entity_id)
                    ),
                    PROJECT_INDEX_SEARCH_INDEX_TABLE.c.type == "entity",
                )
                .values(
                    permalink=case(
                        update_values.permalinks_by_entity_id,
                        value=PROJECT_INDEX_SEARCH_INDEX_TABLE.c.entity_id,
                    )
                )
            )

    async def _write_moved_file_contents(self, content_plan: _MoveContentPlan) -> None:
        """Write planned frontmatter rewrites once the batch has committed.

        Trigger: the batch committed with entity/note_content rows stamped from
        the planned markdown, and the files still hold their pre-move metadata.
        Why: writing files inside the transaction is not atomic with it — a
        rollback would revert the database while the on-disk frontmatter
        rewrites persisted, leaving files ahead of their indexed state.
        Outcome: writes happen only after a successful commit; a failed write
        leaves the file with a checksum that no longer matches its rows, which
        the next scan reconciles as a modified file.
        """
        if self.move_content_updater is None:
            return
        for entity_id, content_update in content_plan.updates_by_entity_id.items():
            try:
                await self.move_content_updater.write_moved_file_content(
                    content_plan.moved_files_by_entity_id[entity_id],
                    content_update,
                )
            except Exception as write_error:
                logger.error(
                    "Failed to write moved file content after move batch commit",
                    path=content_plan.moved_files_by_entity_id[entity_id].new_path,
                    error=str(write_error),
                )

    async def apply_project_index_delete_batch(
        self,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult:
        if not delete_batch.paths:
            return ProjectIndexDeleteBatchResult(deleted_entities=0)

        # --- Snapshot DB eligibility before probing storage ---
        # Only storage-owned rows and fully synchronized notes can become delete
        # candidates. Pending, writing, failed, externally changed, and partially
        # synchronized NoteContent states remain canonical DB work and are skipped.
        async with db.scoped_session(self.session_maker) as session:
            initial_screen = await _load_project_index_delete_candidates(
                session,
                project_id=self.project_id,
                paths=delete_batch.paths,
            )

        initial_candidates_by_path = {
            candidate.entity.file_path: candidate for candidate in initial_screen.candidates
        }
        protected_paths = initial_screen.indexed_paths - initial_candidates_by_path.keys()
        missing_paths = set(delete_batch.paths) - initial_screen.indexed_paths
        skipped_paths = set(protected_paths)
        if protected_paths:
            logger.warning(
                "Skipping planned index deletes for unsynchronized note state",
                paths=tuple(path for path in delete_batch.paths if path in protected_paths),
            )
        if not initial_candidates_by_path:
            return ProjectIndexDeleteBatchResult(
                deleted_entities=0,
                missing_paths=tuple(path for path in delete_batch.paths if path in missing_paths),
                skipped_paths=tuple(path for path in delete_batch.paths if path in skipped_paths),
            )

        # --- Re-probe external storage outside the DB transaction ---
        # A project scan's listing is only a snapshot. Refuse to delete when the
        # runtime cannot positively confirm each candidate is still absent.
        if self.delete_path_verifier is None:
            raise RuntimeError("project-index delete requires a live storage path verifier")
        candidate_paths = tuple(
            path for path in delete_batch.paths if path in initial_candidates_by_path
        )
        confirmed_paths = await self.delete_path_verifier.confirm_deleted_paths(candidate_paths)
        storage_present_paths = set(candidate_paths) - confirmed_paths
        skipped_paths.update(storage_present_paths)
        if storage_present_paths:
            logger.warning(
                "Skipping planned index deletes for paths present in storage again",
                paths=tuple(path for path in delete_batch.paths if path in storage_present_paths),
            )
        if not confirmed_paths:
            return ProjectIndexDeleteBatchResult(
                deleted_entities=0,
                missing_paths=tuple(path for path in delete_batch.paths if path in missing_paths),
                skipped_paths=tuple(path for path in delete_batch.paths if path in skipped_paths),
            )

        # --- Lock and revalidate the exact DB lineage before deletion ---
        async with db.scoped_session(self.session_maker) as session:
            confirmed_candidates = tuple(
                initial_candidates_by_path[path]
                for path in candidate_paths
                if path in confirmed_paths
            )
            candidate_entity_ids = tuple(
                candidate.entity.entity_id for candidate in confirmed_candidates
            )
            await lock_note_content_before_entity_mutation(
                session,
                project_id=self.project_id,
                entity_ids=candidate_entity_ids,
            )
            # PostgreSQL foreign-key insertion takes a lock on Entity. Taking
            # these row locks after NoteContent means a concurrent legacy-note
            # bootstrap either commits before the next statement or waits until
            # this externally deleted entity is gone.
            await session.execute(
                select(Entity.id)
                .where(
                    Entity.project_id == self.project_id,
                    Entity.id.in_(candidate_entity_ids),
                )
                .order_by(Entity.id)
                .with_for_update()
            )
            current_screen = await _load_project_index_delete_candidates(
                session,
                project_id=self.project_id,
                paths=tuple(confirmed_paths),
            )
            current_candidates_by_path = {
                candidate.entity.file_path: candidate for candidate in current_screen.candidates
            }
            deletable_candidates = tuple(
                candidate
                for candidate in confirmed_candidates
                if current_candidates_by_path.get(candidate.entity.file_path) == candidate
            )
            changed_paths = {
                candidate.entity.file_path
                for candidate in confirmed_candidates
                if candidate.entity.file_path in current_screen.indexed_paths
                and current_candidates_by_path.get(candidate.entity.file_path) != candidate
            }
            disappeared_paths = {
                candidate.entity.file_path
                for candidate in confirmed_candidates
                if candidate.entity.file_path not in current_screen.indexed_paths
            }
            skipped_paths.update(changed_paths)
            missing_paths.update(disappeared_paths)
            if changed_paths:
                logger.warning(
                    "Skipping planned index deletes whose canonical lineage changed",
                    paths=tuple(path for path in delete_batch.paths if path in changed_paths),
                )
            if not deletable_candidates:
                return ProjectIndexDeleteBatchResult(
                    deleted_entities=0,
                    missing_paths=tuple(
                        path for path in delete_batch.paths if path in missing_paths
                    ),
                    skipped_paths=tuple(
                        path for path in delete_batch.paths if path in skipped_paths
                    ),
                )

            deleted_entity_ids = tuple(
                candidate.entity.entity_id for candidate in deletable_candidates
            )

            relation_cleanup_entity_ids = await delete_project_index_entities(
                session,
                project_id=self.project_id,
                entity_ids=deleted_entity_ids,
                external_vector_cleaner=self.external_vector_cleaner,
            )

        return ProjectIndexDeleteBatchResult(
            deleted_entities=len(deleted_entity_ids),
            relation_cleanup_entity_ids=relation_cleanup_entity_ids,
            missing_paths=tuple(path for path in delete_batch.paths if path in missing_paths),
            skipped_paths=tuple(path for path in delete_batch.paths if path in skipped_paths),
        )


@dataclass(frozen=True, slots=True)
class InvalidatingProjectIndexBatchStore(
    ProjectIndexMoveBatchStore,
    ProjectIndexDeleteBatchStore,
):
    """Invalidate semantic reads after each durable move or delete batch."""

    move_store: ProjectIndexMoveBatchStore
    delete_store: ProjectIndexDeleteBatchStore
    read_cache: ReadCacheInvalidator
    project_external_id: ProjectExternalId

    @override
    async def apply_project_index_move_batch(
        self,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult:
        async with invalidate_cache(self.read_cache, self.project_external_id):
            return await self.move_store.apply_project_index_move_batch(move_batch)

    @override
    async def apply_project_index_delete_batch(
        self,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult:
        async with invalidate_cache(self.read_cache, self.project_external_id):
            return await self.delete_store.apply_project_index_delete_batch(delete_batch)


@dataclass(frozen=True, slots=True)
class StoreProjectIndexMaintenanceRunner(ProjectIndexMaintenanceRunner):
    """Run project-index maintenance through explicit move/delete batch stores."""

    move_store: ProjectIndexMoveBatchStore
    delete_store: ProjectIndexDeleteBatchStore

    @override
    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
    ) -> ProjectIndexMoveRun:
        return await run_project_index_move_batches(
            moved_files=moved_files,
            batch_size=batch_size,
            move_store=self.move_store,
        )

    @override
    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
    ) -> ProjectIndexDeleteRun:
        return await run_project_index_delete_batches(
            deleted_paths=deleted_paths,
            batch_size=batch_size,
            delete_store=self.delete_store,
        )


@dataclass(frozen=True, slots=True)
class RepositoryProjectIndexMovedEntitySearchRefresher:
    """Refresh search rows for moved entities through explicit sessions."""

    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: ProjectIndexMovedEntityRepository
    entity_indexer: ProjectIndexMovedEntityIndexer

    async def refresh_moved_entities(self, entity_ids: Sequence[int]) -> None:
        unique_entity_ids = sorted(set(entity_ids))
        if not unique_entity_ids:
            return

        async with db.scoped_session(self.session_maker) as session:
            entities = await self.entity_repository.find_by_ids(session, unique_entity_ids)

        entities_by_id = {entity.id: entity for entity in entities}
        missing_entity_ids = [
            entity_id for entity_id in unique_entity_ids if entity_id not in entities_by_id
        ]
        # Trigger: a moved entity id has no row by the time the refresh reloads it.
        # Why: move batches commit before this refresh runs, so a concurrent delete
        #      (file removed, note deleted via API) can legitimately retire the row
        #      in between; failing here would abort the coordinator run before delete
        #      batches and file indexing, stalling the whole scan over a benign race.
        # Outcome: skip the vanished ids (their search rows were removed with the
        #          entity) and refresh the survivors.
        if missing_entity_ids:
            logger.warning(
                "Skipping search refresh for moved entities deleted mid-run",
                entity_ids=missing_entity_ids,
            )

        for entity_id in unique_entity_ids:
            entity = entities_by_id.get(entity_id)
            if entity is None:
                continue
            await self.entity_indexer.index_entity(entity)


def build_project_index_move_batch_plan(
    *,
    moved_files: Mapping[str, str],
    batch_size: int,
) -> ProjectIndexMoveBatchPlan:
    """Build bounded move batches while preserving the caller's path order."""
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    targets = tuple(
        ProjectIndexMoveTarget(old_path=old_path, new_path=new_path)
        for old_path, new_path in moved_files.items()
    )
    batches = tuple(
        ProjectIndexMoveBatch(
            completed_batches=batch_offset // batch_size + 1,
            targets=targets[batch_offset : batch_offset + batch_size],
        )
        for batch_offset in range(0, len(targets), batch_size)
    )
    return ProjectIndexMoveBatchPlan(
        total_moves=len(targets),
        batch_count=len(batches),
        batches=batches,
    )


def build_project_index_delete_batch_plan(
    *,
    deleted_paths: Sequence[str],
    batch_size: int,
) -> ProjectIndexDeleteBatchPlan:
    """Build bounded delete batches while preserving the caller's path order."""
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    paths = tuple(deleted_paths)
    batches = tuple(
        ProjectIndexDeleteBatch(
            completed_batches=batch_offset // batch_size + 1,
            paths=paths[batch_offset : batch_offset + batch_size],
        )
        for batch_offset in range(0, len(paths), batch_size)
    )
    return ProjectIndexDeleteBatchPlan(
        total_deletes=len(paths),
        batch_count=len(batches),
        batches=batches,
    )


async def run_project_index_move_batches(
    *,
    moved_files: Mapping[str, str],
    batch_size: int,
    move_store: ProjectIndexMoveBatchStore,
) -> ProjectIndexMoveRun:
    """Apply project-index move maintenance through a storage adapter."""
    move_plan = build_project_index_move_batch_plan(
        moved_files=moved_files,
        batch_size=batch_size,
    )
    if move_plan.total_moves == 0:
        return ProjectIndexMoveRun(
            total_moves=0,
            total_updated_files=0,
            records=(),
        )

    total_updated = 0
    moved_entity_ids: set[int] = set()
    replaced_entity_ids: set[int] = set()
    relation_cleanup_entity_ids: set[int] = set()
    records: list[ProjectIndexMoveBatchRecord] = []
    for move_batch in move_plan.batches:
        batch_result = await move_store.apply_project_index_move_batch(move_batch)
        total_updated += batch_result.updated_files
        moved_entity_ids.update(batch_result.moved_entity_ids)
        replaced_entity_ids.update(batch_result.replaced_entity_ids)
        relation_cleanup_entity_ids.update(batch_result.relation_cleanup_entity_ids)
        progress = ProjectIndexMoveBatchProgress(
            moved_files=move_plan.total_moves,
            completed_batches=move_batch.completed_batches,
            total_batches=move_plan.batch_count,
            updated_files=total_updated,
        )
        records.append(
            ProjectIndexMoveBatchRecord(
                batch=move_batch,
                result=batch_result,
                progress=progress,
            )
        )

    return ProjectIndexMoveRun(
        total_moves=move_plan.total_moves,
        total_updated_files=total_updated,
        records=tuple(records),
        moved_entity_ids=frozenset(moved_entity_ids),
        replaced_entity_ids=frozenset(replaced_entity_ids),
        relation_cleanup_entity_ids=frozenset(relation_cleanup_entity_ids),
    )


async def run_project_index_delete_batches(
    *,
    deleted_paths: Sequence[str],
    batch_size: int,
    delete_store: ProjectIndexDeleteBatchStore,
) -> ProjectIndexDeleteRun:
    """Apply project-index delete maintenance through a storage adapter."""
    delete_plan = build_project_index_delete_batch_plan(
        deleted_paths=deleted_paths,
        batch_size=batch_size,
    )
    if delete_plan.total_deletes == 0:
        return ProjectIndexDeleteRun(
            total_deletes=0,
            total_deleted_entities=0,
            relation_cleanup_entity_ids=frozenset(),
            records=(),
        )

    total_deleted = 0
    relation_cleanup_entity_ids: set[int] = set()
    records: list[ProjectIndexDeleteBatchRecord] = []
    for delete_batch in delete_plan.batches:
        batch_result = await delete_store.apply_project_index_delete_batch(delete_batch)
        relation_cleanup_entity_ids.update(batch_result.relation_cleanup_entity_ids)
        total_deleted += batch_result.deleted_entities

        progress: ProjectIndexDeleteBatchProgress | None = None
        if batch_result.deleted_entities > 0:
            progress = ProjectIndexDeleteBatchProgress(
                deleted_files=delete_plan.total_deletes,
                completed_batches=delete_batch.completed_batches,
                total_batches=delete_plan.batch_count,
                deleted_entities=total_deleted,
            )

        records.append(
            ProjectIndexDeleteBatchRecord(
                batch=delete_batch,
                result=batch_result,
                progress=progress,
            )
        )

    return ProjectIndexDeleteRun(
        total_deletes=delete_plan.total_deletes,
        total_deleted_entities=total_deleted,
        relation_cleanup_entity_ids=frozenset(relation_cleanup_entity_ids),
        records=tuple(records),
    )
