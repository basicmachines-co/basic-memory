"""Portable project-index workflow request values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, Self

from basic_memory.indexing.project_index_progress import (
    ProjectIndexCompletion,
    ProjectIndexCounters,
    initial_project_index_counters,
    project_index_progress_text,
    should_emit_project_index_progress_event,
)
from basic_memory.indexing.orphan_cleanup import OrphanEntityCleanupResult
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


class ProjectIndexOrphanCleaner(Protocol):
    """Capability that removes indexed rows whose source files disappeared."""

    async def cleanup_orphans(
        self,
        current_paths: set[str],
    ) -> OrphanEntityCleanupResult: ...


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
    ) -> None: ...


class ProjectIndexFanoutFailureRecorder(Protocol):
    """Capability that records a project-index fan-out failure."""

    async def record_project_index_fanout_failure(
        self,
        *,
        workflow_id: WorkflowId,
        error_message: str,
        progress: str,
    ) -> None: ...


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
        )
        for batch_index, batch_targets in enumerate(batches)
    )
    return ProjectIndexBatchJobPlan(
        total_files=len(observed_files),
        batch_count=batch_count,
        batch_requests=batch_requests,
    )


async def run_project_index_coordinator(
    request: RuntimeProjectIndexJobRequest,
    *,
    coordinator_job_id: RuntimeJobId | None,
    observed_file_source: ProjectIndexObservedFileSource,
    orphan_cleaner: ProjectIndexOrphanCleaner,
    workflow_starter: ProjectIndexWorkflowStarter,
    batch_enqueuer: ProjectIndexBatchEnqueuer,
    fanout_failure_recorder: ProjectIndexFanoutFailureRecorder,
    batch_size: int,
) -> ProjectIndexCoordinatorResult:
    """Run the storage-neutral project-index coordinator fan-out."""
    if not request.search:
        raise ValueError("index_project currently requires search=True")

    observed_files = await observed_file_source.list_observed_index_files()
    orphan_cleanup = await orphan_cleaner.cleanup_orphans(
        {observed_file.path for observed_file in observed_files}
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
        observed_files=observed_files,
        batch_size=batch_size,
    )
    completion = await workflow_starter.start_project_index_workflow(
        workflow_request,
        total_files=batch_plan.total_files,
        batch_count=batch_plan.batch_count,
        batch_size=batch_size,
        coordinator_job_id=coordinator_job_id,
    )

    enqueued_files = 0
    enqueued_batches = 0
    try:
        for runtime_request in batch_plan.batch_requests:
            await batch_enqueuer.enqueue_index_file_batch(runtime_request)
            enqueued_batches += 1
            enqueued_files += len(runtime_request.target_paths())
    except Exception as exc:
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
        total_files=batch_plan.total_files,
        enqueued_files=enqueued_files,
        enqueued_batches=enqueued_batches,
        deleted_files=orphan_cleanup.deleted_count,
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
