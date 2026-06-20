"""Typed models for the reusable indexing execution path."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, TYPE_CHECKING

from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexJobRequest,
    EmbeddingIndexTarget,
)
from basic_memory.indexing.file_index_planning import (
    FileIndexDecision,
    FileIndexDecisionStatus,
)
from basic_memory.indexing.project_index_progress import (
    ProjectIndexBatchCounterUpdate,
    ProjectIndexCounters,
    ProjectIndexFileOutcome,
    apply_project_index_batch_outcomes,
    summarize_project_index_file_outcomes,
)
from basic_memory.runtime import (
    NoteExternalId,
    ProjectExternalId,
    ProjectName,
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeNoteActorKind,
    RuntimeNoteActorName,
    RuntimeNoteChangeSource,
    RuntimeNoteObjectMetadataMap,
    RuntimeNoteObjectProvenance,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectChecksumSource,
    StorageEtag,
    TenantId,
    WorkflowId,
    db_version_from_object_metadata,
    normalize_storage_etag,
    storage_object_checksum_for_index_match,
)

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.models import Entity


@dataclass(slots=True)
class IndexFileMetadata:
    """Storage-agnostic metadata for a file queued for indexing."""

    path: str
    size: int
    checksum: str | None = None
    content_type: str | None = None
    last_modified: datetime | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class IndexInputFile(IndexFileMetadata):
    """Fully loaded file payload consumed by the batch executor."""

    content: bytes | None = None


@dataclass(slots=True)
class IndexBatch:
    """A deterministic batch of files bounded by count and total bytes."""

    paths: list[str]
    total_bytes: int


@dataclass(slots=True)
class IndexProgress:
    """Batch indexing progress emitted to callers such as the CLI."""

    files_total: int
    files_processed: int
    batches_total: int
    batches_completed: int
    current_batch_bytes: int = 0
    files_per_minute: float = 0.0
    eta_seconds: float | None = None


@dataclass(slots=True)
class IndexFrontmatterUpdate:
    """A typed frontmatter write request for a single file."""

    path: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class IndexFrontmatterWriteResult:
    """Typed result for a frontmatter write performed during indexing."""

    checksum: str
    content: str


@dataclass(slots=True)
class IndexedEntity:
    """Stable output describing one file that finished indexing successfully."""

    path: str
    entity_id: int
    permalink: str | None
    checksum: str
    content_type: str | None = None
    markdown_content: str | None = None


class FileIndexOperation(StrEnum):
    """Database operation used for one indexed markdown file."""

    created = "created"
    updated = "updated"


def file_index_operation_from_note_object_metadata(
    metadata: RuntimeNoteObjectMetadataMap | None,
) -> FileIndexOperation | None:
    """Infer the file-index operation from accepted note object metadata."""
    db_version = db_version_from_object_metadata(metadata)
    if db_version is None:
        return None
    return FileIndexOperation.created if db_version == 1 else FileIndexOperation.updated


@dataclass(frozen=True, slots=True)
class FileIndexResult:
    """Result for one successfully indexed markdown file."""

    file_path: str
    entity_id: int
    external_id: str
    title: str
    permalink: str
    checksum: str
    operation: FileIndexOperation

    @classmethod
    def from_fields(
        cls,
        *,
        file_path: str,
        entity_id: int,
        external_id: object,
        title: object,
        permalink: object,
        checksum: str,
        operation: FileIndexOperation,
    ) -> FileIndexResult:
        """Validate entity fields loaded for a completed file-index result."""
        return cls(
            file_path=file_path,
            entity_id=entity_id,
            external_id=_required_file_index_result_text(
                external_id,
                field_name="external_id",
                file_path=file_path,
            ),
            title=_required_file_index_result_text(
                title,
                field_name="title",
                file_path=file_path,
            ),
            permalink=_required_file_index_result_text(
                permalink,
                field_name="permalink",
                file_path=file_path,
            ),
            checksum=checksum,
            operation=operation,
        )


def _required_file_index_result_text(
    value: object,
    *,
    field_name: str,
    file_path: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Indexed entity for {file_path} is missing {field_name}")
    return value.strip()


class IndexFileJobStatus(StrEnum):
    """Normal outcomes for an index-file job."""

    processed = "processed"
    current = "current"
    missing = "missing"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class IndexFileJobResult:
    """Summary of what happened while processing one file-index job."""

    status: IndexFileJobStatus
    reason: str
    entity_id: int | None = None
    note_external_id: str | None = None
    title: str | None = None
    permalink: str | None = None
    entity_checksum: str | None = None
    operation: FileIndexOperation | None = None
    actor_user_profile_id: str | None = None
    actor_kind: str | None = None
    actor_name: str | None = None
    live_update_source: str | None = None


@dataclass(frozen=True, slots=True)
class IndexFileEmbeddingJobContext:
    """Index-file facts needed to decide whether embeddings should be queued."""

    tenant_id: TenantId
    project_id: int
    index_embeddings: bool
    result: IndexFileJobResult


def plan_index_file_embedding_job(
    context: IndexFileEmbeddingJobContext,
) -> EmbeddingIndexJobRequest | None:
    """Plan single-entity embedding work after one file-index job."""
    if not context.index_embeddings:
        return None
    if context.result.status != IndexFileJobStatus.processed:
        return None
    if context.result.entity_id is None:
        raise RuntimeError("index_file processed without an entity id")
    if context.result.entity_checksum is None:
        raise RuntimeError("index_file processed without an entity checksum")

    return EmbeddingIndexJobRequest(
        tenant_id=context.tenant_id,
        project_id=context.project_id,
        entity_id=context.result.entity_id,
        entity_checksum=context.result.entity_checksum,
    )


class IndexFileNoteLiveUpdateType(StrEnum):
    """Note-level live-update events produced by file indexing."""

    note_created = "note.created"
    note_updated = "note.updated"


@dataclass(frozen=True, slots=True)
class IndexFileNoteLiveUpdateContext:
    """Queue-neutral context needed to plan one note-level file-index update."""

    tenant_id: TenantId
    project_external_id: ProjectExternalId | None
    project_name: ProjectName | None
    file_path: RuntimeFilePath
    mode: RuntimeStorageFileIndexMode
    workflow_id: WorkflowId | None = None
    object_etag: StorageEtag | None = None
    object_size: int | None = None


@dataclass(frozen=True, slots=True)
class IndexFileNoteLiveUpdatePlan:
    """Typed note-level update that an adapter can publish through its transport."""

    event_type: IndexFileNoteLiveUpdateType
    tenant_id: TenantId
    source: RuntimeNoteChangeSource
    project_external_id: ProjectExternalId
    project_name: ProjectName
    note_external_id: NoteExternalId
    note_path: RuntimeFilePath
    note_version_etag: StorageEtag | None
    content_checksum: RuntimeFileChecksum | None
    file_checksum: StorageEtag | None
    file_size_bytes: int | None
    title: str
    permalink: str
    actor_user_profile_id: str | None = None
    actor_kind: RuntimeNoteActorKind | None = None
    actor_name: RuntimeNoteActorName | None = None


DEFAULT_INDEX_FILE_NOTE_LIVE_UPDATE_SOURCE: RuntimeNoteChangeSource = "s3_webhook"


def index_file_job_result_from_decision(
    decision: FileIndexDecision,
) -> IndexFileJobResult:
    """Convert a terminal file-index decision into a job result."""
    if decision.status == FileIndexDecisionStatus.current:
        return IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason=decision.reason,
        )
    if decision.status == FileIndexDecisionStatus.missing:
        return IndexFileJobResult(
            status=IndexFileJobStatus.missing,
            reason=decision.reason,
        )
    raise RuntimeError(f"Unexpected file index decision: {decision.status}")


def index_file_job_result_from_indexed_file(
    indexed_file: FileIndexResult,
    *,
    live_update_plan: IndexedFileLiveUpdatePlan | None = None,
) -> IndexFileJobResult:
    """Convert a successful file-index result into the queue job result."""
    operation = indexed_file.operation
    if live_update_plan is not None and live_update_plan.operation is not None:
        operation = live_update_plan.operation

    return IndexFileJobResult(
        status=IndexFileJobStatus.processed,
        reason=f"file indexed: {indexed_file.file_path}",
        entity_id=indexed_file.entity_id,
        note_external_id=indexed_file.external_id,
        title=indexed_file.title,
        permalink=indexed_file.permalink,
        entity_checksum=indexed_file.checksum,
        operation=operation,
        actor_user_profile_id=(
            live_update_plan.actor_user_profile_id if live_update_plan is not None else None
        ),
        actor_kind=live_update_plan.actor_kind if live_update_plan is not None else None,
        actor_name=live_update_plan.actor_name if live_update_plan is not None else None,
        live_update_source=(
            live_update_plan.live_update_source if live_update_plan is not None else None
        ),
    )


def plan_index_file_note_live_update(
    context: IndexFileNoteLiveUpdateContext,
    result: IndexFileJobResult,
) -> IndexFileNoteLiveUpdatePlan | None:
    """Plan a note-level live update for one externally observed file index."""
    if context.workflow_id is not None:
        return None
    if context.mode != RuntimeStorageFileIndexMode.observed_object:
        return None
    if result.status == IndexFileJobStatus.current and result.live_update_source is None:
        return None
    if result.status not in (IndexFileJobStatus.processed, IndexFileJobStatus.current):
        return None

    project_external_id = _required_index_file_note_live_update_text(
        context.project_external_id,
        field_name="project_external_id",
    )
    project_name = _required_index_file_note_live_update_text(
        context.project_name,
        field_name="project_name",
    )
    note_external_id = _required_index_file_note_live_update_text(
        result.note_external_id,
        field_name="note_external_id",
    )
    title = _required_index_file_note_live_update_text(
        result.title,
        field_name="title",
    )
    permalink = _required_index_file_note_live_update_text(
        result.permalink,
        field_name="permalink",
    )
    if result.operation is None:
        raise RuntimeError("Observed_object index_file result is missing operation")

    event_type = (
        IndexFileNoteLiveUpdateType.note_created
        if result.operation == FileIndexOperation.created
        else IndexFileNoteLiveUpdateType.note_updated
    )
    normalized_etag = (
        normalize_storage_etag(context.object_etag) if context.object_etag is not None else None
    )
    return IndexFileNoteLiveUpdatePlan(
        event_type=event_type,
        tenant_id=context.tenant_id,
        source=result.live_update_source or DEFAULT_INDEX_FILE_NOTE_LIVE_UPDATE_SOURCE,
        project_external_id=project_external_id,
        project_name=project_name,
        note_external_id=note_external_id,
        note_path=context.file_path,
        note_version_etag=normalized_etag,
        content_checksum=result.entity_checksum,
        file_checksum=normalized_etag,
        file_size_bytes=context.object_size,
        title=title,
        permalink=permalink,
        actor_user_profile_id=result.actor_user_profile_id,
        actor_kind=result.actor_kind,
        actor_name=result.actor_name,
    )


def _required_index_file_note_live_update_text(
    value: object,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Observed_object index_file result is missing {field_name}")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CurrentMaterializedNoteEntity:
    """Indexed entity state needed to build a current materialized note result."""

    entity_id: int
    external_id: str
    title: str
    permalink: str
    checksum: RuntimeFileChecksum | None

    @classmethod
    def from_fields(
        cls,
        *,
        entity_id: int,
        external_id: object,
        title: object,
        permalink: object,
        checksum: object,
        file_path: str,
    ) -> CurrentMaterializedNoteEntity:
        """Validate entity fields loaded for a current materialized note."""
        return cls(
            entity_id=entity_id,
            external_id=_required_current_materialized_note_text(
                external_id,
                field_name="external_id",
                file_path=file_path,
            ),
            title=_required_current_materialized_note_text(
                title,
                field_name="title",
                file_path=file_path,
            ),
            permalink=_required_current_materialized_note_text(
                permalink,
                field_name="permalink",
                file_path=file_path,
            ),
            checksum=str(checksum) if checksum is not None else None,
        )


@dataclass(frozen=True, slots=True)
class CurrentMaterializedNotePlan:
    """Planned current-file result plus checksum diagnostics for adapter logging."""

    job_result: IndexFileJobResult
    requires_entity: bool = False
    object_checksum_source: RuntimeStorageObjectChecksumSource | None = None
    object_checksum: RuntimeFileChecksum | None = None
    entity_checksum: RuntimeFileChecksum | None = None
    source: RuntimeNoteChangeSource | None = None
    checksum_matches_entity: bool | None = None


@dataclass(frozen=True, slots=True)
class IndexedFileLiveUpdatePlan:
    """Trusted live-update metadata for one freshly indexed file."""

    object_checksum_source: RuntimeStorageObjectChecksumSource
    object_checksum: RuntimeFileChecksum
    indexed_checksum: RuntimeFileChecksum
    checksum_matches_indexed_file: bool
    metadata_actor_user_profile_id: str | None = None
    metadata_actor_kind: str | None = None
    metadata_actor_name: str | None = None
    metadata_source: RuntimeNoteChangeSource | None = None
    actor_user_profile_id: str | None = None
    actor_kind: str | None = None
    actor_name: str | None = None
    live_update_source: RuntimeNoteChangeSource | None = None
    operation: FileIndexOperation | None = None


def _required_current_materialized_note_text(
    value: object,
    *,
    field_name: str,
    file_path: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Current entity for {file_path} is missing {field_name}")
    return value.strip()


def plan_current_materialized_note_result(
    *,
    reason: str,
    file_path: str,
    object_checksum: RuntimeFileChecksum,
    object_metadata: RuntimeNoteObjectMetadataMap | None,
    entity: CurrentMaterializedNoteEntity | None,
) -> CurrentMaterializedNotePlan:
    """Plan a current file-index result for a DB-first materialized note."""
    current_result = IndexFileJobResult(status=IndexFileJobStatus.current, reason=reason)

    provenance = RuntimeNoteObjectProvenance.from_object_metadata(object_metadata)
    if provenance.source is None:
        return CurrentMaterializedNotePlan(job_result=current_result)

    live_update_operation = file_index_operation_from_note_object_metadata(object_metadata)
    if live_update_operation is None:
        return CurrentMaterializedNotePlan(
            job_result=current_result,
            source=provenance.source,
        )

    if entity is None:
        return CurrentMaterializedNotePlan(
            job_result=current_result,
            requires_entity=True,
            source=provenance.source,
        )

    selected_checksum = storage_object_checksum_for_index_match(
        object_checksum=object_checksum,
        object_metadata=object_metadata,
    )
    checksum_matches_entity = selected_checksum.checksum == entity.checksum
    plan = CurrentMaterializedNotePlan(
        job_result=current_result,
        object_checksum_source=selected_checksum.source,
        object_checksum=selected_checksum.checksum,
        entity_checksum=entity.checksum,
        source=provenance.source,
        checksum_matches_entity=checksum_matches_entity,
    )
    if not checksum_matches_entity:
        return plan

    return CurrentMaterializedNotePlan(
        job_result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason=reason,
            entity_id=entity.entity_id,
            note_external_id=entity.external_id,
            title=entity.title,
            permalink=entity.permalink,
            entity_checksum=entity.checksum,
            operation=live_update_operation,
            actor_user_profile_id=provenance.actor_user_profile_id,
            actor_kind=provenance.actor_kind,
            actor_name=provenance.actor_name,
            live_update_source=provenance.source,
        ),
        object_checksum_source=plan.object_checksum_source,
        object_checksum=plan.object_checksum,
        entity_checksum=plan.entity_checksum,
        source=plan.source,
        checksum_matches_entity=True,
    )


def plan_indexed_file_live_update_metadata(
    *,
    indexed_file: FileIndexResult,
    object_checksum: RuntimeFileChecksum,
    object_metadata: RuntimeNoteObjectMetadataMap | None,
) -> IndexedFileLiveUpdatePlan:
    """Plan trusted actor/source metadata for a freshly indexed file."""
    selected_checksum = storage_object_checksum_for_index_match(
        object_checksum=object_checksum,
        object_metadata=object_metadata,
    )
    provenance = RuntimeNoteObjectProvenance.from_object_metadata(object_metadata)
    checksum_matches_indexed_file = selected_checksum.checksum == indexed_file.checksum
    plan = IndexedFileLiveUpdatePlan(
        object_checksum_source=selected_checksum.source,
        object_checksum=selected_checksum.checksum,
        indexed_checksum=indexed_file.checksum,
        checksum_matches_indexed_file=checksum_matches_indexed_file,
        metadata_actor_user_profile_id=provenance.actor_user_profile_id,
        metadata_actor_kind=provenance.actor_kind,
        metadata_actor_name=provenance.actor_name,
        metadata_source=provenance.source,
    )
    if not checksum_matches_indexed_file:
        return plan

    return IndexedFileLiveUpdatePlan(
        object_checksum_source=plan.object_checksum_source,
        object_checksum=plan.object_checksum,
        indexed_checksum=plan.indexed_checksum,
        checksum_matches_indexed_file=True,
        metadata_actor_user_profile_id=plan.metadata_actor_user_profile_id,
        metadata_actor_kind=plan.metadata_actor_kind,
        metadata_actor_name=plan.metadata_actor_name,
        metadata_source=plan.metadata_source,
        actor_user_profile_id=provenance.actor_user_profile_id,
        actor_kind=provenance.actor_kind,
        actor_name=provenance.actor_name,
        live_update_source=provenance.source,
        operation=file_index_operation_from_note_object_metadata(object_metadata),
    )


@dataclass(frozen=True, slots=True)
class IndexFileBatchJobResult:
    """Summary of what happened while processing one file-index batch job."""

    total_files: int
    processed_files: int
    missing_files: int
    failed_files: int
    file_results: tuple[IndexFileJobResult, ...]
    vector_targets: tuple[EmbeddingIndexTarget, ...]


def project_index_file_outcome_from_job_result(
    result: IndexFileJobResult,
) -> ProjectIndexFileOutcome:
    """Map one file job result to the aggregate project-index outcome."""
    return ProjectIndexFileOutcome(result.status.value)


def project_index_file_outcomes_from_job_results(
    results: Sequence[IndexFileJobResult],
) -> tuple[ProjectIndexFileOutcome, ...]:
    """Map file job results to aggregate project-index outcomes."""
    return tuple(project_index_file_outcome_from_job_result(result) for result in results)


def apply_project_index_batch_job_results(
    *,
    counters: ProjectIndexCounters,
    recorded_batch_indexes: Sequence[int],
    batch_index: int,
    batch_count: int,
    results: Sequence[IndexFileJobResult],
) -> ProjectIndexBatchCounterUpdate:
    """Apply one batch's file job results exactly once to aggregate counters."""
    return apply_project_index_batch_outcomes(
        counters=counters,
        recorded_batch_indexes=recorded_batch_indexes,
        batch_index=batch_index,
        batch_count=batch_count,
        outcomes=project_index_file_outcomes_from_job_results(results),
    )


def build_index_file_batch_job_result(
    *,
    target_paths: Sequence[str],
    terminal_results: Mapping[str, IndexFileJobResult],
    indexed_files: Sequence[IndexedEntity],
    errors: Mapping[str, str],
    index_embeddings: bool,
    embedding_eligible_paths: Collection[str],
) -> IndexFileBatchJobResult:
    """Build an ordered batch result after content reads and indexing complete."""
    indexed_by_path = {indexed.path: indexed for indexed in indexed_files}
    ordered_file_results: list[IndexFileJobResult] = []
    vector_targets: list[EmbeddingIndexTarget] = []

    for file_path in target_paths:
        indexed = indexed_by_path.get(file_path)
        if indexed is not None and index_embeddings and file_path in embedding_eligible_paths:
            vector_targets.append(
                EmbeddingIndexTarget(
                    entity_id=indexed.entity_id,
                    entity_checksum=indexed.checksum,
                )
            )

        if file_path in errors:
            ordered_file_results.append(
                IndexFileJobResult(
                    status=IndexFileJobStatus.failed,
                    reason=f"file indexing failed: {file_path}: {errors[file_path]}",
                    entity_id=indexed.entity_id if indexed is not None else None,
                    entity_checksum=indexed.checksum if indexed is not None else None,
                )
            )
        elif indexed is not None:
            ordered_file_results.append(
                IndexFileJobResult(
                    status=IndexFileJobStatus.processed,
                    reason=f"file indexed: {file_path}",
                    entity_id=indexed.entity_id,
                    entity_checksum=indexed.checksum,
                )
            )
        else:
            terminal_result = terminal_results.get(file_path)
            if terminal_result is None:
                raise RuntimeError(f"Missing file index result for {file_path}")
            ordered_file_results.append(terminal_result)

    outcome_summary = summarize_project_index_file_outcomes(
        project_index_file_outcomes_from_job_results(ordered_file_results)
    )
    return IndexFileBatchJobResult(
        total_files=outcome_summary.total_files,
        processed_files=outcome_summary.processed_files,
        missing_files=outcome_summary.missing_files,
        failed_files=outcome_summary.failed_files,
        file_results=tuple(ordered_file_results),
        vector_targets=tuple(vector_targets),
    )


@dataclass(slots=True)
class SyncedMarkdownFile:
    """Canonical result for syncing one markdown file end-to-end."""

    entity: Entity
    checksum: str
    markdown_content: str
    file_path: str
    content_type: str
    updated_at: datetime
    size: int


@dataclass(slots=True)
class IndexingBatchResult:
    """Outcome for one batch execution."""

    indexed: list[IndexedEntity] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    relations_resolved: int = 0
    relations_unresolved: int = 0
    search_indexed: int = 0


class IndexFileWriter(Protocol):
    """Narrow protocol for frontmatter writes during indexing."""

    async def write_frontmatter(
        self, update: IndexFrontmatterUpdate
    ) -> IndexFrontmatterWriteResult: ...


class IndexEntitySearchWriter(Protocol):
    """Narrow protocol for search writes during indexing."""

    async def index_entity_data(self, entity: Entity, content: str | None = None) -> None: ...
