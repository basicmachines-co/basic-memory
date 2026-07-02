"""Storage-neutral project-index coordinator fan-out."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, Self

from basic_memory.indexing.change_planning import ChangeReport
from basic_memory.indexing.embedding_index_planning import (
    EmbeddingBatchVectorSync,
    EmbeddingIndexBatchJobRequest,
    run_embedding_index_batch,
)
from basic_memory.indexing.models import IndexFileBatchJobResult
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexMaintenanceRunner,
    ProjectIndexMovedEntitySearchRefresher,
)
from basic_memory.indexing.project_index_progress import ProjectIndexCompletion
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
)


class ProjectIndexRequestSource(Protocol):
    """Minimal source shape for project-index requests."""

    project_id: ProjectId
    project_external_id: ProjectExternalId
    project_name: ProjectName | None
    project_permalink: ProjectPermalink | None
    project_path: ProjectPath
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


class ProjectIndexWorkflowStarter(Protocol):
    """Capability that starts product-visible project-index workflow progress."""

    async def start_project_index_workflow(
        self,
        request: ProjectIndexRequest,
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
    """Capability that records a project-index fan-out failure.

    Adapters that persist failures against a durable workflow carry that
    workflow identity themselves; the coordinator only reports what failed.
    """

    async def record_project_index_fanout_failure(
        self,
        *,
        error_message: str,
        progress: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectIndexRequest:
    """Project-index target identity and mode flags."""

    project: ProjectRuntimeReference
    force_full: bool
    search: bool
    embeddings: bool

    @classmethod
    def from_source(cls, source: ProjectIndexRequestSource) -> Self:
        """Build a project-index request from queue payloads or boundary models."""
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
            **self.project.workflow_metadata(),
            "force_full": self.force_full,
            "search": self.search,
            "embeddings": self.embeddings,
        }


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


def build_project_index_batch_job_plan(
    *,
    request: ProjectIndexRequest,
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
            project=request.project,
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
    embedding_vector_sync: EmbeddingBatchVectorSync | None = None,
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
    moved_entity_ids_to_refresh = move_run.moved_entity_ids | move_run.relation_cleanup_entity_ids
    if moved_entity_ids_to_refresh:
        await moved_entity_search_refresher.refresh_moved_entities(
            sorted(moved_entity_ids_to_refresh)
        )
    delete_run = await maintenance_runner.run_delete_batches(
        deleted_paths=change_report.deleted_files,
        batch_size=batch_size,
    )
    index_request = ProjectIndexRequest(
        project=request.project,
        force_full=request.force_full,
        search=request.search,
        embeddings=request.embeddings,
    )
    batch_plan = build_project_index_batch_job_plan(
        request=index_request,
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
            index_request,
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
                error_message=(
                    "Failed to enqueue project index batch jobs after "
                    f"{enqueued_files}/{batch_plan.total_files} files: {exc}"
                ),
                progress="fan-out failed",
            )
        raise

    await sync_project_index_vector_targets(
        request=request,
        batch_results=batch_results,
        embedding_vector_sync=embedding_vector_sync,
    )

    return ProjectIndexCoordinatorResult(
        total_files=len(observed_files),
        enqueued_files=enqueued_files,
        enqueued_batches=enqueued_batches,
        deleted_files=delete_run.total_deleted_entities,
        moved_files=move_run.total_updated_files,
        relation_cleanup_entity_ids=(
            move_run.relation_cleanup_entity_ids | delete_run.relation_cleanup_entity_ids
        ),
        batch_results=tuple(batch_results),
        completion=completion,
    )


async def sync_project_index_vector_targets(
    *,
    request: RuntimeProjectIndexJobRequest,
    batch_results: Sequence[IndexFileBatchJobResult],
    embedding_vector_sync: EmbeddingBatchVectorSync | None,
) -> None:
    """Refresh vectors produced by inline project-index batch execution."""
    if not request.embeddings or embedding_vector_sync is None:
        return

    vector_targets = tuple(
        target for batch_result in batch_results for target in batch_result.vector_targets
    )
    if not vector_targets:
        return

    await run_embedding_index_batch(
        EmbeddingIndexBatchJobRequest(
            project_id=request.project.project_id,
            project_path=request.project.project_path,
            entities=vector_targets,
        ),
        vector_sync=embedding_vector_sync,
    )
