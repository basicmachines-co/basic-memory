"""Portable project-index workflow request values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, Self

from sqlalchemy import bindparam, case, column, delete, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.models import (
    IndexFileBatchJobResult,
    IndexFileJobResult,
    apply_project_index_batch_job_results,
    project_index_file_outcome_from_job_result,
)
from basic_memory.indexing.change_planning import ChangeReport
from basic_memory.indexing.project_index_progress import (
    ProjectIndexCompletion,
    ProjectIndexCounters,
    ProjectIndexMetadataReporter,
    apply_project_index_file_outcome,
    initial_project_index_counters,
    project_index_counters_from_metadata,
    project_index_missing_batches_from_metadata,
    project_index_progress_text,
    project_index_recorded_batches_from_metadata,
    should_emit_project_index_progress_event,
)
from basic_memory.models import Entity, NoteContent, Relation
from basic_memory.runtime import (
    ProjectExternalId,
    ProjectId,
    ProjectName,
    ProjectPath,
    ProjectPermalink,
    ProjectRuntimeReference,
    RuntimeIndexFileBatchJobRequest,
    RuntimeJobId,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
    RuntimeQueuedWorkflowMetadata,
    RuntimeWorkflowBroker,
    RuntimeWorkflowTransport,
    TenantId,
    WorkflowId,
)


class ProjectIndexWorkflowSource(Protocol):
    """Minimal source shape for project-index workflow requests."""

    tenant_id: TenantId
    project_id: ProjectId
    project_external_id: ProjectExternalId
    project_name: ProjectName | None
    project_permalink: ProjectPermalink | None
    project_path: ProjectPath
    workflow_id: WorkflowId
    force_full: bool
    search: bool
    embeddings: bool


class ProjectIndexObservedFileSource(Protocol):
    """Capability that lists the current storage objects eligible for indexing."""

    async def list_observed_index_files(self) -> tuple[RuntimeObservedIndexFile, ...]: ...


class ProjectIndexChangeDetector(Protocol):
    """Capability that compares observed storage files with indexed project state."""

    async def detect_all_changes(
        self,
        storage_files: Mapping[str, RuntimeObservedIndexFile],
    ) -> ChangeReport: ...


class ProjectIndexMaintenanceRunner(Protocol):
    """Capability that applies project-wide move/delete maintenance."""

    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
        metadata_reporter: ProjectIndexMetadataReporter | None = None,
    ) -> ProjectIndexMoveRun: ...

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
        metadata_reporter: ProjectIndexMetadataReporter | None = None,
    ) -> ProjectIndexDeleteRun: ...


class ProjectIndexWorkflowStarter(Protocol):
    """Capability that starts product-visible project-index workflow progress."""

    async def start_project_index_workflow(
        self,
        request: ProjectIndexWorkflowRequest,
        *,
        total_files: int,
        batch_count: int,
        batch_size: int,
        coordinator_job_id: RuntimeJobId | None,
    ) -> ProjectIndexCompletion | None: ...


class ProjectIndexBatchEnqueuer(Protocol):
    """Capability that queues one child file-index batch request."""

    async def enqueue_index_file_batch(
        self,
        request: RuntimeIndexFileBatchJobRequest,
    ) -> IndexFileBatchJobResult | None: ...


class ProjectIndexFanoutFailureRecorder(Protocol):
    """Capability that records a project-index fan-out failure."""

    async def record_project_index_fanout_failure(
        self,
        *,
        workflow_id: WorkflowId,
        error_message: str,
        progress: str,
    ) -> None: ...


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


class ProjectIndexMoveBatchStore(Protocol):
    """Capability that applies one project-index move-maintenance batch."""

    async def apply_project_index_move_batch(
        self,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult: ...


class ProjectIndexDeleteBatchStore(Protocol):
    """Capability that applies one project-index delete-maintenance batch."""

    async def apply_project_index_delete_batch(
        self,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult: ...


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
class ProjectIndexWorkflowRequest:
    """Project-index workflow identity and mode flags."""

    tenant_id: TenantId
    workflow_id: WorkflowId
    project: ProjectRuntimeReference
    force_full: bool
    search: bool
    embeddings: bool

    @classmethod
    def from_source(cls, source: ProjectIndexWorkflowSource) -> Self:
        """Build a workflow request from queue payloads or boundary models."""
        project_external_id = str(source.project_external_id).strip()
        if not project_external_id:
            raise ValueError(f"Project {source.project_id} is missing external_id")

        project_path = str(source.project_path).strip()
        if not project_path:
            raise ValueError(f"Project {source.project_id} is missing path")

        project_name = str(source.project_name).strip() if source.project_name else None
        project_permalink = (
            str(source.project_permalink).strip() if source.project_permalink else None
        )
        return cls(
            tenant_id=source.tenant_id,
            workflow_id=source.workflow_id,
            project=ProjectRuntimeReference(
                project_id=source.project_id,
                project_external_id=project_external_id,
                project_name=project_name,
                project_permalink=project_permalink,
                project_path=project_path,
            ),
            force_full=source.force_full,
            search=source.search,
            embeddings=source.embeddings,
        )

    def workflow_payload_metadata(self) -> dict[str, object]:
        """Serialize to the existing workflow metadata payload shape."""
        return {
            "tenant_id": str(self.tenant_id),
            **self.project.workflow_metadata(),
            "force_full": self.force_full,
            "search": self.search,
            "embeddings": self.embeddings,
        }


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowStart:
    """Portable start metadata for a project-index workflow."""

    counters: ProjectIndexCounters
    progress: str
    metadata: dict[str, object]
    attempt_event_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowProgressUpdate:
    """Portable progress metadata for a running project-index workflow."""

    counters: ProjectIndexCounters
    progress: str
    should_emit_event: bool
    metadata: dict[str, object]
    progress_event_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowCompletionUpdate:
    """Portable completion metadata for a successful project-index workflow."""

    counters: ProjectIndexCounters
    progress: str
    metadata: dict[str, object]
    completed_event_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowFailureUpdate:
    """Portable failure metadata for a project-index workflow."""

    counters: ProjectIndexCounters
    progress: str
    error_message: str
    metadata: dict[str, object]
    failed_event_data: dict[str, object]


type ProjectIndexWorkflowStartStatus = Literal["running", "complete"]


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowStartPlan:
    """Portable decision for starting one project-index workflow."""

    status: ProjectIndexWorkflowStartStatus
    workflow_start: ProjectIndexWorkflowStart
    completion_update: ProjectIndexWorkflowCompletionUpdate | None = None

    def __post_init__(self) -> None:
        if self.status == "running":
            if self.completion_update is not None:
                raise ValueError("running start plans cannot include a completion update")
            return

        if self.completion_update is None:
            raise ValueError("complete start plans require a completion update")

    @classmethod
    def running(cls, workflow_start: ProjectIndexWorkflowStart) -> Self:
        """Return a non-terminal start plan."""
        return cls(status="running", workflow_start=workflow_start)

    @classmethod
    def complete(
        cls,
        *,
        workflow_start: ProjectIndexWorkflowStart,
        completion_update: ProjectIndexWorkflowCompletionUpdate,
    ) -> Self:
        """Return an immediately terminal start plan."""
        return cls(
            status="complete",
            workflow_start=workflow_start,
            completion_update=completion_update,
        )

    @property
    def is_complete(self) -> bool:
        """Return whether the workflow should complete immediately after starting."""
        return self.status == "complete"

    def require_completion_update(self) -> ProjectIndexWorkflowCompletionUpdate:
        """Return the completion update or fail when this is a running plan."""
        if self.completion_update is None:
            raise RuntimeError(f"{self.status} plan does not include a completion update")
        return self.completion_update


type ProjectIndexWorkflowRecordStatus = Literal["progress", "complete", "already_recorded"]


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowRecordPlan:
    """Portable decision for applying one child result to aggregate workflow state."""

    status: ProjectIndexWorkflowRecordStatus
    progress_update: ProjectIndexWorkflowProgressUpdate | None = None
    completion_update: ProjectIndexWorkflowCompletionUpdate | None = None

    def __post_init__(self) -> None:
        if self.status == "already_recorded":
            if self.progress_update is not None or self.completion_update is not None:
                raise ValueError("already_recorded plans cannot include updates")
            return

        if self.progress_update is None:
            raise ValueError(f"{self.status} plans require a progress update")

        if self.status == "progress" and self.completion_update is not None:
            raise ValueError("progress plans cannot include a completion update")
        if self.status == "complete" and self.completion_update is None:
            raise ValueError("complete plans require a completion update")

    @classmethod
    def progress(
        cls,
        progress_update: ProjectIndexWorkflowProgressUpdate,
    ) -> Self:
        """Return a running progress plan."""
        return cls(status="progress", progress_update=progress_update)

    @classmethod
    def complete(
        cls,
        *,
        progress_update: ProjectIndexWorkflowProgressUpdate,
        completion_update: ProjectIndexWorkflowCompletionUpdate,
    ) -> Self:
        """Return a terminal success plan."""
        return cls(
            status="complete",
            progress_update=progress_update,
            completion_update=completion_update,
        )

    @classmethod
    def already_recorded(cls) -> Self:
        """Return an idempotent no-op plan."""
        return cls(status="already_recorded")

    @property
    def is_complete(self) -> bool:
        """Return whether this plan completes the workflow."""
        return self.status == "complete"

    @property
    def should_emit_progress_event(self) -> bool:
        """Return whether the runtime should append a progress event."""
        return (
            self.status == "progress"
            and self.progress_update is not None
            and self.progress_update.should_emit_event
        )

    def require_progress_update(self) -> ProjectIndexWorkflowProgressUpdate:
        """Return the progress update or fail when this is an idempotent no-op."""
        if self.progress_update is None:
            raise RuntimeError(f"{self.status} plan does not include a progress update")
        return self.progress_update

    def require_completion_update(self) -> ProjectIndexWorkflowCompletionUpdate:
        """Return the completion update or fail when the plan is not terminal."""
        if self.completion_update is None:
            raise RuntimeError(f"{self.status} plan does not include a completion update")
        return self.completion_update


type ProjectIndexStaleWorkflowStatus = Literal["keep_running", "fail"]


@dataclass(frozen=True, slots=True)
class ProjectIndexStaleWorkflowPlan:
    """Portable decision for one stale project-index workflow check."""

    status: ProjectIndexStaleWorkflowStatus
    activity_update: ProjectIndexBatchJobActivityUpdate | None = None
    failure_update: ProjectIndexWorkflowFailureUpdate | None = None

    def __post_init__(self) -> None:
        if self.status == "keep_running":
            if self.activity_update is None:
                raise ValueError("keep_running plans require an activity update")
            if self.failure_update is not None:
                raise ValueError("keep_running plans cannot include a failure update")
            return

        if self.failure_update is None:
            raise ValueError("fail plans require a failure update")
        if self.activity_update is not None:
            raise ValueError("fail plans cannot include an activity update")

    @classmethod
    def keep_running(
        cls,
        activity_update: ProjectIndexBatchJobActivityUpdate,
    ) -> Self:
        """Return a non-terminal activity update plan."""
        return cls(status="keep_running", activity_update=activity_update)

    @classmethod
    def fail(
        cls,
        failure_update: ProjectIndexWorkflowFailureUpdate,
    ) -> Self:
        """Return a terminal stale-failure plan."""
        return cls(status="fail", failure_update=failure_update)

    @property
    def should_fail(self) -> bool:
        """Return whether this stale check should fail the workflow."""
        return self.status == "fail"

    def require_activity_update(self) -> ProjectIndexBatchJobActivityUpdate:
        """Return the activity update or fail when this is a terminal plan."""
        if self.activity_update is None:
            raise RuntimeError(f"{self.status} plan does not include an activity update")
        return self.activity_update

    def require_failure_update(self) -> ProjectIndexWorkflowFailureUpdate:
        """Return the failure update or fail when this is a keep-running plan."""
        if self.failure_update is None:
            raise RuntimeError(f"{self.status} plan does not include a failure update")
        return self.failure_update


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowQueued:
    """Portable queued metadata for a project-index workflow handoff."""

    logical_key: str
    metadata: dict[str, object]
    queued_event_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProjectIndexBatchJobPlan:
    """Portable project-index child batch job requests."""

    total_files: int
    batch_count: int
    batch_requests: tuple[RuntimeIndexFileBatchJobRequest, ...]


@dataclass(frozen=True, slots=True)
class ProjectIndexCoordinatorResult:
    """Summary of one project-index coordinator fan-out run."""

    total_files: int
    enqueued_files: int
    enqueued_batches: int
    deleted_files: int
    moved_files: int = 0
    relation_cleanup_entity_ids: frozenset[int] = frozenset()
    batch_results: tuple[IndexFileBatchJobResult, ...] = ()
    completion: ProjectIndexCompletion | None = None


@dataclass(frozen=True, slots=True)
class ProjectIndexBatchJobActivity:
    """Unfinished project-index child batch jobs observed by a runtime adapter."""

    batch_indexes: tuple[int, ...]
    queued_count: int
    picked_fresh_count: int
    picked_stale_count: int

    @classmethod
    def empty(cls) -> Self:
        """Return an activity snapshot with no unfinished child jobs."""
        return cls(
            batch_indexes=(),
            queued_count=0,
            picked_fresh_count=0,
            picked_stale_count=0,
        )

    @property
    def has_unfinished_jobs(self) -> bool:
        return bool(self.batch_indexes)

    def workflow_metadata(self, *, observed_at: str) -> dict[str, object]:
        """Serialize to the existing stale-workflow activity metadata shape."""
        if not observed_at:
            raise ValueError("observed_at is required")
        return {
            "active_batches": list(self.batch_indexes),
            "queued_count": self.queued_count,
            "picked_fresh_count": self.picked_fresh_count,
            "picked_stale_count": self.picked_stale_count,
            "observed_at": observed_at,
        }


@dataclass(frozen=True, slots=True)
class ProjectIndexBatchJobActivityUpdate:
    """Workflow metadata after observing unfinished child batch activity."""

    activity: ProjectIndexBatchJobActivity
    metadata: dict[str, object]


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

PROJECT_INDEX_SEARCH_INDEX_TABLE = table(
    "search_index",
    column("project_id"),
    column("entity_id"),
    column("type"),
    column("file_path"),
    column("permalink"),
)


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

            surviving_relation_sources = await session.execute(
                select(Relation.from_id)
                .where(
                    Relation.project_id == self.project_id,
                    Relation.to_id.in_(deleted_entity_ids),
                    Relation.from_id.not_in(deleted_entity_ids),
                )
                .distinct()
            )
            relation_cleanup_entity_ids = frozenset(
                int(entity_id) for entity_id in surviving_relation_sources.scalars()
            )

            delete_params = {
                "project_id": self.project_id,
                "deleted_entity_ids": deleted_entity_ids,
                "relation_row_type": "relation",
            }
            await session.execute(DELETE_PROJECT_INDEX_SEARCH_ROWS_SQL, delete_params)
            await session.execute(DELETE_PROJECT_INDEX_VECTOR_CHUNKS_SQL, delete_params)
            await session.execute(
                delete(Entity).where(
                    Entity.project_id == self.project_id,
                    Entity.id.in_(deleted_entity_ids),
                )
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

    move_store: ProjectIndexMoveBatchStore
    delete_store: ProjectIndexDeleteBatchStore

    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
        metadata_reporter: ProjectIndexMetadataReporter | None = None,
    ) -> ProjectIndexMoveRun:
        return await run_project_index_move_batches(
            moved_files=moved_files,
            batch_size=batch_size,
            move_store=self.move_store,
            metadata_reporter=metadata_reporter,
        )

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
        metadata_reporter: ProjectIndexMetadataReporter | None = None,
    ) -> ProjectIndexDeleteRun:
        return await run_project_index_delete_batches(
            deleted_paths=deleted_paths,
            batch_size=batch_size,
            delete_store=self.delete_store,
            metadata_reporter=metadata_reporter,
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
        if missing_entity_ids:
            raise RuntimeError(
                "Moved entities disappeared before search refresh: "
                f"{', '.join(str(entity_id) for entity_id in missing_entity_ids)}"
            )

        for entity_id in unique_entity_ids:
            await self.entity_indexer.index_entity(entities_by_id[entity_id])


def project_index_workflow_logical_key(
    *,
    tenant_id: TenantId,
    project_name: ProjectName | None,
    force_full: bool,
    search: bool,
    embeddings: bool,
) -> str:
    """Return the legacy project-index workflow dedupe key."""
    logical_key = f"index-{tenant_id}-{project_name or 'all'}"
    if force_full:
        logical_key = f"{logical_key}-full"
    if not search:
        logical_key = f"{logical_key}-emb"
    elif not embeddings:
        logical_key = f"{logical_key}-search"
    return logical_key


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
    metadata_reporter: ProjectIndexMetadataReporter | None = None,
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
    records: list[ProjectIndexMoveBatchRecord] = []
    for move_batch in move_plan.batches:
        batch_result = await move_store.apply_project_index_move_batch(move_batch)
        total_updated += batch_result.updated_files
        moved_entity_ids.update(batch_result.moved_entity_ids)
        progress = ProjectIndexMoveBatchProgress(
            moved_files=move_plan.total_moves,
            completed_batches=move_batch.completed_batches,
            total_batches=move_plan.batch_count,
            updated_files=total_updated,
        )
        if metadata_reporter is not None:
            await metadata_reporter.report_progress(progress.workflow_metadata())
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
    )


async def run_project_index_delete_batches(
    *,
    deleted_paths: Sequence[str],
    batch_size: int,
    delete_store: ProjectIndexDeleteBatchStore,
    metadata_reporter: ProjectIndexMetadataReporter | None = None,
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
            if metadata_reporter is not None:
                await metadata_reporter.report_progress(progress.workflow_metadata())

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


def build_project_index_workflow_queued(
    *,
    request: ProjectIndexWorkflowRequest,
    transport_broker: RuntimeWorkflowBroker,
    transport_entrypoint: str,
) -> ProjectIndexWorkflowQueued:
    """Build queued workflow metadata before the coordinator starts."""
    logical_key = project_index_workflow_logical_key(
        tenant_id=request.tenant_id,
        project_name=request.project.project_name,
        force_full=request.force_full,
        search=request.search,
        embeddings=request.embeddings,
    )
    queued_metadata = RuntimeQueuedWorkflowMetadata(
        workflow_id=request.workflow_id,
        progress="queued for index",
        payload=request.workflow_payload_metadata(),
        transport=RuntimeWorkflowTransport(
            broker=transport_broker,
            entrypoint=transport_entrypoint,
        ),
    )

    return ProjectIndexWorkflowQueued(
        logical_key=logical_key,
        metadata=queued_metadata.workflow_metadata(),
        queued_event_data={
            "logical_key": logical_key,
            "entrypoint": transport_entrypoint,
            "phase": "queued",
            "progress": "queued for index",
            **request.project.workflow_metadata(),
        },
    )


def build_project_index_batch_job_plan(
    *,
    request: ProjectIndexWorkflowRequest,
    observed_files: Sequence[RuntimeObservedIndexFile],
    batch_size: int,
) -> ProjectIndexBatchJobPlan:
    """Build runtime child job requests for one project-index fan-out."""
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    batches = tuple(
        tuple(observed_files[index : index + batch_size])
        for index in range(0, len(observed_files), batch_size)
    )
    batch_count = len(batches)
    batch_requests = tuple(
        RuntimeIndexFileBatchJobRequest(
            tenant_id=request.tenant_id,
            project=request.project,
            workflow_id=request.workflow_id,
            batch_index=batch_index,
            batch_count=batch_count,
            file_paths=tuple(target.path for target in batch_targets),
            observed_files=batch_targets,
            index_embeddings=request.embeddings,
            force_full=request.force_full,
        )
        for batch_index, batch_targets in enumerate(batches)
    )
    return ProjectIndexBatchJobPlan(
        total_files=len(observed_files),
        batch_count=batch_count,
        batch_requests=batch_requests,
    )


def project_index_storage_files_from_observed(
    observed_files: Sequence[RuntimeObservedIndexFile],
) -> dict[str, RuntimeObservedIndexFile]:
    """Map observed files by project-relative path for change detection."""
    return {observed_file.path: observed_file for observed_file in observed_files}


def select_project_index_target_files(
    *,
    observed_files: Sequence[RuntimeObservedIndexFile],
    change_report: ChangeReport,
    force_full: bool,
) -> tuple[RuntimeObservedIndexFile, ...]:
    """Select observed files that should be submitted to file-index batches."""
    if force_full:
        return tuple(observed_files)

    target_paths = set(change_report.new_files) | set(change_report.modified_files)
    return tuple(
        observed_file for observed_file in observed_files if observed_file.path in target_paths
    )


async def run_project_index_coordinator(
    request: RuntimeProjectIndexJobRequest,
    *,
    coordinator_job_id: RuntimeJobId | None,
    observed_file_source: ProjectIndexObservedFileSource,
    change_detector: ProjectIndexChangeDetector,
    maintenance_runner: ProjectIndexMaintenanceRunner,
    moved_entity_search_refresher: ProjectIndexMovedEntitySearchRefresher,
    workflow_starter: ProjectIndexWorkflowStarter | None,
    batch_enqueuer: ProjectIndexBatchEnqueuer,
    fanout_failure_recorder: ProjectIndexFanoutFailureRecorder | None,
    batch_size: int,
) -> ProjectIndexCoordinatorResult:
    """Run the storage-neutral project-index coordinator fan-out."""
    if not request.search:
        raise ValueError("index_project currently requires search=True")

    observed_files = await observed_file_source.list_observed_index_files()
    change_report = await change_detector.detect_all_changes(
        project_index_storage_files_from_observed(observed_files)
    )
    move_run = await maintenance_runner.run_move_batches(
        moved_files=change_report.moved_files,
        batch_size=batch_size,
    )
    if move_run.moved_entity_ids:
        await moved_entity_search_refresher.refresh_moved_entities(
            sorted(move_run.moved_entity_ids)
        )
    delete_run = await maintenance_runner.run_delete_batches(
        deleted_paths=change_report.deleted_files,
        batch_size=batch_size,
    )
    workflow_request = ProjectIndexWorkflowRequest(
        tenant_id=request.tenant_id,
        workflow_id=request.workflow_id,
        project=request.project,
        force_full=request.force_full,
        search=request.search,
        embeddings=request.embeddings,
    )
    batch_plan = build_project_index_batch_job_plan(
        request=workflow_request,
        observed_files=select_project_index_target_files(
            observed_files=observed_files,
            change_report=change_report,
            force_full=request.force_full,
        ),
        batch_size=batch_size,
    )
    completion = None
    if workflow_starter is not None:
        completion = await workflow_starter.start_project_index_workflow(
            workflow_request,
            total_files=batch_plan.total_files,
            batch_count=batch_plan.batch_count,
            batch_size=batch_size,
            coordinator_job_id=coordinator_job_id,
        )

    enqueued_files = 0
    enqueued_batches = 0
    batch_results: list[IndexFileBatchJobResult] = []
    try:
        for runtime_request in batch_plan.batch_requests:
            batch_result = await batch_enqueuer.enqueue_index_file_batch(runtime_request)
            if batch_result is not None:
                batch_results.append(batch_result)
            enqueued_batches += 1
            enqueued_files += len(runtime_request.target_paths())
    except Exception as exc:
        if fanout_failure_recorder is not None:
            await fanout_failure_recorder.record_project_index_fanout_failure(
                workflow_id=request.workflow_id,
                error_message=(
                    "Failed to enqueue project index batch jobs after "
                    f"{enqueued_files}/{batch_plan.total_files} files: {exc}"
                ),
                progress="fan-out failed",
            )
        raise

    return ProjectIndexCoordinatorResult(
        total_files=len(observed_files),
        enqueued_files=enqueued_files,
        enqueued_batches=enqueued_batches,
        deleted_files=delete_run.total_deleted_entities,
        moved_files=move_run.total_updated_files,
        relation_cleanup_entity_ids=delete_run.relation_cleanup_entity_ids,
        batch_results=tuple(batch_results),
        completion=completion,
    )


def build_project_index_batch_activity_update(
    *,
    metadata: Mapping[str, object],
    activity: ProjectIndexBatchJobActivity,
    observed_at: str,
) -> ProjectIndexBatchJobActivityUpdate:
    """Build metadata that records unfinished child batch job activity."""
    updated_metadata = dict(metadata)
    updated_metadata["last_batch_job_activity"] = activity.workflow_metadata(
        observed_at=observed_at
    )
    return ProjectIndexBatchJobActivityUpdate(
        activity=activity,
        metadata=updated_metadata,
    )


def build_project_index_workflow_start(
    *,
    request: ProjectIndexWorkflowRequest,
    total_files: int,
    batch_count: int,
    batch_size: int,
    discovered_at: str,
    transport_broker: RuntimeWorkflowBroker,
    transport_entrypoint: str,
    transport_job_id: RuntimeJobId | None,
) -> ProjectIndexWorkflowStart:
    """Build the initial persisted metadata for a project-index workflow."""
    counters = initial_project_index_counters(total_files)
    progress = project_index_progress_text(counters)
    payload = request.workflow_payload_metadata()
    pgq_job_id = str(transport_job_id) if transport_job_id is not None else None
    metadata: dict[str, object] = {
        "phase": "indexing",
        "progress": progress,
        "payload": payload,
        "discovery": {
            "total_files": total_files,
            "batch_count": batch_count,
            "batch_size": batch_size,
            "discovered_at": discovered_at,
        },
        "counters": counters.to_metadata(),
        "transport": {
            "broker": transport_broker,
            "entrypoint": transport_entrypoint,
            "pgq_job_id": pgq_job_id,
        },
    }
    return ProjectIndexWorkflowStart(
        counters=counters,
        progress=progress,
        metadata=metadata,
        attempt_event_data={
            "phase": "indexing",
            "progress": progress,
            "total_files": total_files,
            "batch_count": batch_count,
            "batch_size": batch_size,
            "pgq_job_id": pgq_job_id,
            "project_id": request.project.project_id,
            "project_name": request.project.project_name,
            "project_permalink": request.project.project_permalink,
            "project_path": request.project.project_path,
        },
    )


def plan_project_index_workflow_start(
    *,
    request: ProjectIndexWorkflowRequest,
    total_files: int,
    batch_count: int,
    batch_size: int,
    discovered_at: str,
    transport_broker: RuntimeWorkflowBroker,
    transport_entrypoint: str,
    transport_job_id: RuntimeJobId | None,
) -> ProjectIndexWorkflowStartPlan:
    """Plan initial workflow metadata and immediate completion for empty projects."""
    workflow_start = build_project_index_workflow_start(
        request=request,
        total_files=total_files,
        batch_count=batch_count,
        batch_size=batch_size,
        discovered_at=discovered_at,
        transport_broker=transport_broker,
        transport_entrypoint=transport_entrypoint,
        transport_job_id=transport_job_id,
    )
    if total_files == 0:
        return ProjectIndexWorkflowStartPlan.complete(
            workflow_start=workflow_start,
            completion_update=build_project_index_workflow_completion_update(
                metadata=workflow_start.metadata,
                counters=workflow_start.counters,
                progress=workflow_start.progress,
            ),
        )
    return ProjectIndexWorkflowStartPlan.running(workflow_start)


def build_project_index_workflow_progress_update(
    *,
    metadata: Mapping[str, object],
    counters: ProjectIndexCounters,
    recorded_batch_indexes: Sequence[int] | None = None,
) -> ProjectIndexWorkflowProgressUpdate:
    """Build updated persisted metadata for a running project-index workflow."""
    progress = project_index_progress_text(counters)
    counters_metadata = counters.to_metadata()
    updated_metadata = dict(metadata)
    updated_metadata["phase"] = "indexing"
    updated_metadata["progress"] = progress
    updated_metadata["counters"] = counters_metadata
    if recorded_batch_indexes is not None:
        updated_metadata["recorded_batches"] = list(recorded_batch_indexes)

    return ProjectIndexWorkflowProgressUpdate(
        counters=counters,
        progress=progress,
        should_emit_event=should_emit_project_index_progress_event(counters),
        metadata=updated_metadata,
        progress_event_data={
            "phase": "indexing",
            "progress": progress,
            "payload": updated_metadata.get("payload") or {},
            "counters": counters_metadata,
        },
    )


def build_project_index_workflow_completion_update(
    *,
    metadata: Mapping[str, object],
    counters: ProjectIndexCounters,
    progress: str,
) -> ProjectIndexWorkflowCompletionUpdate:
    """Build terminal success metadata for a project-index workflow."""
    counters_metadata = counters.to_metadata()
    completed_metadata = dict(metadata)
    completed_metadata["phase"] = "completed"
    completed_metadata["progress"] = progress
    completed_metadata["counters"] = counters_metadata
    completed_metadata["result"] = counters_metadata

    return ProjectIndexWorkflowCompletionUpdate(
        counters=counters,
        progress=progress,
        metadata=completed_metadata,
        completed_event_data={
            "phase": "completed",
            "progress": progress,
            "payload": completed_metadata.get("payload") or {},
            "result": counters_metadata,
        },
    )


def require_project_index_workflow_counters(
    metadata: Mapping[str, object],
    *,
    workflow_id: WorkflowId,
) -> ProjectIndexCounters:
    """Read required aggregate counters from project-index workflow metadata."""
    if not metadata.get("counters"):
        raise RuntimeError(f"Project index workflow counters are missing: {workflow_id}")
    return project_index_counters_from_metadata(metadata, workflow_id=workflow_id)


def plan_project_index_file_result_record(
    *,
    metadata: Mapping[str, object],
    workflow_id: WorkflowId,
    result: IndexFileJobResult,
) -> ProjectIndexWorkflowRecordPlan:
    """Plan one child file result update for a project-index workflow."""
    counters = require_project_index_workflow_counters(
        metadata,
        workflow_id=workflow_id,
    )
    counters = apply_project_index_file_outcome(
        counters,
        project_index_file_outcome_from_job_result(result),
    )
    progress_update = build_project_index_workflow_progress_update(
        metadata=metadata,
        counters=counters,
    )
    if counters.processed >= counters.total:
        return ProjectIndexWorkflowRecordPlan.complete(
            progress_update=progress_update,
            completion_update=build_project_index_workflow_completion_update(
                metadata=progress_update.metadata,
                counters=counters,
                progress=progress_update.progress,
            ),
        )
    return ProjectIndexWorkflowRecordPlan.progress(progress_update)


def plan_project_index_batch_result_record(
    *,
    metadata: Mapping[str, object],
    workflow_id: WorkflowId,
    batch_index: int,
    batch_count: int,
    results: Sequence[IndexFileJobResult],
) -> ProjectIndexWorkflowRecordPlan:
    """Plan one idempotent child batch result update for a project-index workflow."""
    counters = require_project_index_workflow_counters(
        metadata,
        workflow_id=workflow_id,
    )
    batch_update = apply_project_index_batch_job_results(
        counters=counters,
        recorded_batch_indexes=project_index_recorded_batches_from_metadata(metadata),
        batch_index=batch_index,
        batch_count=batch_count,
        results=results,
    )
    if batch_update.already_recorded:
        return ProjectIndexWorkflowRecordPlan.already_recorded()

    counters = batch_update.counters
    progress_update = build_project_index_workflow_progress_update(
        metadata=metadata,
        counters=counters,
        recorded_batch_indexes=batch_update.recorded_batch_indexes,
    )
    if batch_update.is_complete:
        return ProjectIndexWorkflowRecordPlan.complete(
            progress_update=progress_update,
            completion_update=build_project_index_workflow_completion_update(
                metadata=progress_update.metadata,
                counters=counters,
                progress=progress_update.progress,
            ),
        )
    return ProjectIndexWorkflowRecordPlan.progress(progress_update)


def plan_project_index_stale_workflow(
    *,
    metadata: Mapping[str, object],
    workflow_id: WorkflowId,
    active_batch_jobs: ProjectIndexBatchJobActivity,
    observed_at: str,
    last_heartbeat_at: str,
    stale_before: str,
) -> ProjectIndexStaleWorkflowPlan:
    """Plan how a runtime should update one stale project-index workflow."""
    if active_batch_jobs.has_unfinished_jobs:
        return ProjectIndexStaleWorkflowPlan.keep_running(
            build_project_index_batch_activity_update(
                metadata=metadata,
                activity=active_batch_jobs,
                observed_at=observed_at,
            )
        )

    counters = require_project_index_workflow_counters(
        metadata,
        workflow_id=workflow_id,
    )
    missing_batch_plan = project_index_missing_batches_from_metadata(metadata)
    return ProjectIndexStaleWorkflowPlan.fail(
        build_project_index_workflow_stale_failure_update(
            metadata=metadata,
            counters=counters,
            missing_batch_indexes=missing_batch_plan.missing_batch_indexes,
            recorded_batch_indexes=missing_batch_plan.recorded_batch_indexes,
            legacy_missing_batch_count=missing_batch_plan.legacy_missing_batch_count,
            last_heartbeat_at=last_heartbeat_at,
            stale_before=stale_before,
        )
    )


def build_project_index_workflow_stale_failure_update(
    *,
    metadata: Mapping[str, object],
    counters: ProjectIndexCounters,
    missing_batch_indexes: Sequence[int],
    recorded_batch_indexes: Sequence[int],
    legacy_missing_batch_count: int,
    last_heartbeat_at: str,
    stale_before: str,
) -> ProjectIndexWorkflowFailureUpdate:
    """Build terminal failure metadata for stale project-index batch fan-out."""
    missing_batches = list(missing_batch_indexes)
    recorded_batches = list(recorded_batch_indexes)
    if legacy_missing_batch_count:
        error_message = "Project index stalled with legacy batch metadata"
    else:
        error_message = f"Project index stalled with {len(missing_batches)} unreported batch(es)"
    progress = f"Project index stalled after {counters.processed}/{counters.total} files"
    diagnostics: dict[str, object] = {
        "reason": "stale_project_index_batches",
        "missing_batches": missing_batches,
        "recorded_batches": recorded_batches,
        "legacy_missing_batch_count": legacy_missing_batch_count,
        "last_heartbeat_at": last_heartbeat_at,
        "stale_before": stale_before,
    }
    counters_metadata = counters.to_metadata()
    failed_metadata = dict(metadata)
    failed_metadata["phase"] = "failed"
    failed_metadata["progress"] = progress
    failed_metadata["counters"] = counters_metadata
    failed_metadata["diagnostics"] = diagnostics

    return ProjectIndexWorkflowFailureUpdate(
        counters=counters,
        progress=progress,
        error_message=error_message,
        metadata=failed_metadata,
        failed_event_data={
            "phase": "failed",
            "progress": progress,
            "payload": failed_metadata.get("payload") or {},
            "error": error_message,
            "diagnostics": diagnostics,
        },
    )
