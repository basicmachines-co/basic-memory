"""Project-index move/delete maintenance for indexed project state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from loguru import logger
from sqlalchemy import bindparam, case, column, delete, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, NoteContent, Relation
from basic_memory.repository.project_repository import _load_sqlite_vec_on_session
from basic_memory.runtime.storage import ProjectId


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


@dataclass(frozen=True, slots=True)
class ProjectIndexMovedFile:
    """One indexed file move that may need storage-backed metadata repair."""

    entity_id: int
    old_path: str
    new_path: str
    old_permalink: str | None


@dataclass(frozen=True, slots=True)
class ProjectIndexMovedFileContentUpdate:
    """Storage result after rewriting a moved file's markdown metadata."""

    permalink: str
    checksum: str
    markdown_content: str


class ProjectIndexMoveContentUpdater(Protocol):
    """Capability that applies provider-specific moved-file content repair."""

    async def update_moved_file_content(
        self,
        session: AsyncSession,
        moved_file: ProjectIndexMovedFile,
    ) -> ProjectIndexMovedFileContentUpdate | None: ...


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

DELETE_PROJECT_INDEX_VECTOR_CHUNKS_SQL = text("""
    DELETE FROM search_vector_chunks
    WHERE project_id = :project_id
      AND entity_id IN :deleted_entity_ids
""").bindparams(bindparam("deleted_entity_ids", expanding=True))

SELECT_PROJECT_INDEX_SQLITE_VECTOR_TABLES_SQL = text("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
      AND name IN ('search_vector_chunks', 'search_vector_embeddings')
""")

SELECT_PROJECT_INDEX_POSTGRES_VECTOR_TABLES_SQL = text("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = ANY (current_schemas(false))
      AND table_name IN ('search_vector_chunks', 'search_vector_embeddings')
""")

DELETE_PROJECT_INDEX_SQLITE_VECTOR_EMBEDDINGS_SQL = text("""
    DELETE FROM search_vector_embeddings
    WHERE rowid IN (
        SELECT id
        FROM search_vector_chunks
        WHERE project_id = :project_id
          AND entity_id IN :deleted_entity_ids
    )
""").bindparams(bindparam("deleted_entity_ids", expanding=True))

DELETE_PROJECT_INDEX_POSTGRES_VECTOR_EMBEDDINGS_SQL = text("""
    DELETE FROM search_vector_embeddings
    WHERE chunk_id IN (
        SELECT id
        FROM search_vector_chunks
        WHERE project_id = :project_id
          AND entity_id IN :deleted_entity_ids
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


def project_index_session_dialect_name(session: AsyncSession) -> str:
    """Return the SQLAlchemy dialect name for project-index maintenance."""
    return session.get_bind().dialect.name


async def project_index_vector_table_names(session: AsyncSession) -> frozenset[str]:
    """Return available vector table names for the current database backend."""
    dialect_name = project_index_session_dialect_name(session)
    if dialect_name == "sqlite":
        result = await session.execute(SELECT_PROJECT_INDEX_SQLITE_VECTOR_TABLES_SQL)
    elif dialect_name == "postgresql":
        result = await session.execute(SELECT_PROJECT_INDEX_POSTGRES_VECTOR_TABLES_SQL)
    else:
        raise RuntimeError(f"Unsupported project-index database dialect: {dialect_name}")

    return frozenset(str(table_name) for table_name in result.scalars())


async def delete_project_index_vector_rows(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_ids: Sequence[int],
) -> None:
    """Delete backend vector rows for project-index entity deletes when tables exist."""
    deleted_entity_ids = tuple(entity_ids)
    if not deleted_entity_ids:
        return

    vector_table_names = await project_index_vector_table_names(session)
    if "search_vector_chunks" not in vector_table_names:
        return

    delete_params = {
        "project_id": project_id,
        "deleted_entity_ids": deleted_entity_ids,
    }
    if "search_vector_embeddings" in vector_table_names:
        dialect_name = project_index_session_dialect_name(session)
        if dialect_name == "sqlite":
            if await _load_sqlite_vec_on_session(session):
                await session.execute(
                    DELETE_PROJECT_INDEX_SQLITE_VECTOR_EMBEDDINGS_SQL,
                    delete_params,
                )
        elif dialect_name == "postgresql":
            await session.execute(
                DELETE_PROJECT_INDEX_POSTGRES_VECTOR_EMBEDDINGS_SQL,
                delete_params,
            )
        else:
            raise RuntimeError(f"Unsupported project-index database dialect: {dialect_name}")

    await session.execute(DELETE_PROJECT_INDEX_VECTOR_CHUNKS_SQL, delete_params)


async def delete_project_index_entities(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_ids: Sequence[int],
) -> frozenset[int]:
    """Delete indexed entities and return surviving relation sources needing repair."""
    deleted_entity_ids = tuple(entity_ids)
    if not deleted_entity_ids:
        return frozenset()

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
    await delete_project_index_vector_rows(
        session,
        project_id=project_id,
        entity_ids=deleted_entity_ids,
    )
    await session.execute(
        delete(Entity).where(
            Entity.project_id == project_id,
            Entity.id.in_(deleted_entity_ids),
        )
    )
    return relation_cleanup_entity_ids


@dataclass(frozen=True, slots=True)
class RepositoryProjectIndexMaintenanceStore:
    """Apply project-index move/delete maintenance with explicit sessions."""

    session_maker: async_sessionmaker[AsyncSession]
    project_id: ProjectId
    move_content_updater: ProjectIndexMoveContentUpdater | None = None

    async def apply_project_index_move_batch(
        self,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult:
        if not move_batch.targets:
            return ProjectIndexMoveBatchResult(updated_files=0)

        target_paths_by_old_path = {
            move_target.old_path: move_target.new_path for move_target in move_batch.targets
        }
        old_paths = tuple(target_paths_by_old_path)

        async with db.scoped_session(self.session_maker) as session:
            existing_paths_result = await session.execute(
                select(Entity.id, Entity.file_path, Entity.permalink).where(
                    Entity.project_id == self.project_id,
                    Entity.file_path.in_(old_paths),
                )
            )
            target_rows = existing_paths_result.mappings().all()
            updated_old_paths = frozenset(str(row["file_path"]) for row in target_rows)
            target_paths_by_entity_id = {
                int(row["id"]): target_paths_by_old_path[str(row["file_path"])]
                for row in target_rows
            }
            replaced_entity_ids: frozenset[int] = frozenset()
            relation_cleanup_entity_ids: frozenset[int] = frozenset()
            content_updates_by_entity_id: dict[int, ProjectIndexMovedFileContentUpdate] = {}
            if self.move_content_updater is not None:
                for row in target_rows:
                    entity_id = int(row["id"])
                    old_path = str(row["file_path"])
                    content_update = await self.move_content_updater.update_moved_file_content(
                        session,
                        ProjectIndexMovedFile(
                            entity_id=entity_id,
                            old_path=old_path,
                            new_path=target_paths_by_old_path[old_path],
                            old_permalink=(
                                str(row["permalink"]) if row["permalink"] is not None else None
                            ),
                        ),
                    )
                    if content_update is not None:
                        content_updates_by_entity_id[entity_id] = content_update

            if updated_old_paths:
                new_paths = tuple(sorted(set(target_paths_by_entity_id.values())))
                replacement_result = await session.execute(
                    select(Entity.id, Entity.file_path).where(
                        Entity.project_id == self.project_id,
                        Entity.file_path.in_(new_paths),
                        Entity.id.not_in(tuple(target_paths_by_entity_id)),
                    )
                )
                replacement_rows = replacement_result.mappings().all()
                replaced_entity_ids = frozenset(int(row["id"]) for row in replacement_rows)
                relation_cleanup_entity_ids = await delete_project_index_entities(
                    session,
                    project_id=self.project_id,
                    entity_ids=tuple(replaced_entity_ids),
                )

                entity_update_values = {
                    "file_path": case(
                        target_paths_by_old_path,
                        value=Entity.file_path,
                    )
                }
                note_content_update_values = {
                    "file_path": case(
                        target_paths_by_entity_id,
                        value=NoteContent.entity_id,
                    )
                }
                search_index_update_values = {
                    "file_path": case(
                        target_paths_by_entity_id,
                        value=PROJECT_INDEX_SEARCH_INDEX_TABLE.c.entity_id,
                    )
                }
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
                    entity_update_values["checksum"] = case(
                        checksums_by_entity_id,
                        value=Entity.id,
                        else_=Entity.checksum,
                    )
                    entity_update_values["permalink"] = case(
                        permalinks_by_entity_id,
                        value=Entity.id,
                        else_=Entity.permalink,
                    )
                    note_content_update_values["db_checksum"] = case(
                        checksums_by_entity_id,
                        value=NoteContent.entity_id,
                        else_=NoteContent.db_checksum,
                    )
                    note_content_update_values["file_checksum"] = case(
                        checksums_by_entity_id,
                        value=NoteContent.entity_id,
                        else_=NoteContent.file_checksum,
                    )
                    note_content_update_values["markdown_content"] = case(
                        markdown_by_entity_id,
                        value=NoteContent.entity_id,
                        else_=NoteContent.markdown_content,
                    )

                await session.execute(
                    update(Entity)
                    .where(
                        Entity.project_id == self.project_id,
                        Entity.file_path.in_(updated_old_paths),
                    )
                    .values(**entity_update_values)
                )
                await session.execute(
                    update(NoteContent)
                    .where(
                        NoteContent.project_id == self.project_id,
                        NoteContent.entity_id.in_(tuple(target_paths_by_entity_id)),
                    )
                    .values(**note_content_update_values)
                )
                await session.execute(
                    update(PROJECT_INDEX_SEARCH_INDEX_TABLE)
                    .where(
                        PROJECT_INDEX_SEARCH_INDEX_TABLE.c.project_id == self.project_id,
                        PROJECT_INDEX_SEARCH_INDEX_TABLE.c.entity_id.in_(
                            tuple(target_paths_by_entity_id)
                        ),
                    )
                    .values(**search_index_update_values)
                )
                if content_updates_by_entity_id:
                    await session.execute(
                        update(PROJECT_INDEX_SEARCH_INDEX_TABLE)
                        .where(
                            PROJECT_INDEX_SEARCH_INDEX_TABLE.c.project_id == self.project_id,
                            PROJECT_INDEX_SEARCH_INDEX_TABLE.c.entity_id.in_(
                                tuple(content_updates_by_entity_id)
                            ),
                            PROJECT_INDEX_SEARCH_INDEX_TABLE.c.type == "entity",
                        )
                        .values(
                            permalink=case(
                                permalinks_by_entity_id,
                                value=PROJECT_INDEX_SEARCH_INDEX_TABLE.c.entity_id,
                            )
                        )
                    )

        missing_paths = tuple(
            move_target.old_path
            for move_target in move_batch.targets
            if move_target.old_path not in updated_old_paths
        )
        return ProjectIndexMoveBatchResult(
            updated_files=len(updated_old_paths),
            moved_entity_ids=frozenset(target_paths_by_entity_id),
            replaced_entity_ids=replaced_entity_ids,
            relation_cleanup_entity_ids=relation_cleanup_entity_ids,
            missing_paths=missing_paths,
        )

    async def apply_project_index_delete_batch(
        self,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult:
        if not delete_batch.paths:
            return ProjectIndexDeleteBatchResult(deleted_entities=0)

        async with db.scoped_session(self.session_maker) as session:
            target_result = await session.execute(
                select(Entity.id, Entity.file_path).where(
                    Entity.project_id == self.project_id,
                    Entity.file_path.in_(tuple(delete_batch.paths)),
                )
            )
            target_rows = target_result.mappings().all()

            if not target_rows:
                return ProjectIndexDeleteBatchResult(
                    deleted_entities=0,
                    missing_paths=tuple(delete_batch.paths),
                )

            deleted_entity_ids = tuple(int(row["id"]) for row in target_rows)
            deleted_found_paths = frozenset(str(row["file_path"]) for row in target_rows)

            relation_cleanup_entity_ids = await delete_project_index_entities(
                session,
                project_id=self.project_id,
                entity_ids=deleted_entity_ids,
            )

        return ProjectIndexDeleteBatchResult(
            deleted_entities=len(deleted_entity_ids),
            relation_cleanup_entity_ids=relation_cleanup_entity_ids,
            missing_paths=tuple(
                deleted_path
                for deleted_path in delete_batch.paths
                if deleted_path not in deleted_found_paths
            ),
        )


@dataclass(frozen=True, slots=True)
class StoreProjectIndexMaintenanceRunner(ProjectIndexMaintenanceRunner):
    """Run project-index maintenance through explicit move/delete batch stores."""

    move_store: RepositoryProjectIndexMaintenanceStore
    delete_store: RepositoryProjectIndexMaintenanceStore

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
    move_store: RepositoryProjectIndexMaintenanceStore,
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
    delete_store: RepositoryProjectIndexMaintenanceStore,
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
