"""Portable project-index workflow metadata planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Self

from basic_memory.indexing.models import (
    IndexFileJobResult,
    apply_project_index_batch_job_results,
    project_index_file_outcome_from_job_result,
)
from basic_memory.indexing.project_index_coordinator import ProjectIndexRequest
from basic_memory.indexing.project_index_progress import (
    ProjectIndexCounters,
    apply_project_index_file_outcome,
    initial_project_index_counters,
    project_index_counters_from_metadata,
    project_index_missing_batches_from_metadata,
    project_index_progress_text,
    project_index_recorded_batches_from_metadata,
    should_emit_project_index_progress_event,
)
from basic_memory.runtime.workflows import WorkflowId


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
class ProjectIndexWorkflowStartRunning:
    """Non-terminal start: child batches remain to fan out."""

    workflow_start: ProjectIndexWorkflowStart


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowStartComplete:
    """Terminal start: an empty project completes immediately after starting."""

    workflow_start: ProjectIndexWorkflowStart
    completion_update: ProjectIndexWorkflowCompletionUpdate


type ProjectIndexWorkflowStartPlan = (
    ProjectIndexWorkflowStartRunning | ProjectIndexWorkflowStartComplete
)


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowRecordProgress:
    """Running update: the child result advanced aggregate counters."""

    progress_update: ProjectIndexWorkflowProgressUpdate


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowRecordComplete:
    """Terminal update: the child result finished the last outstanding file."""

    progress_update: ProjectIndexWorkflowProgressUpdate
    completion_update: ProjectIndexWorkflowCompletionUpdate


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowAlreadyRecorded:
    """Idempotent no-op: this batch already updated aggregate counters."""


type ProjectIndexWorkflowRecordPlan = (
    ProjectIndexWorkflowRecordProgress
    | ProjectIndexWorkflowRecordComplete
    | ProjectIndexWorkflowAlreadyRecorded
)


@dataclass(frozen=True, slots=True)
class ProjectIndexStaleWorkflowKeepRunning:
    """Non-terminal stale check: unfinished child jobs were observed."""

    activity_update: ProjectIndexBatchJobActivityUpdate


@dataclass(frozen=True, slots=True)
class ProjectIndexStaleWorkflowFail:
    """Terminal stale check: no child activity remains, so the workflow fails."""

    failure_update: ProjectIndexWorkflowFailureUpdate


type ProjectIndexStaleWorkflowPlan = (
    ProjectIndexStaleWorkflowKeepRunning | ProjectIndexStaleWorkflowFail
)


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
    request: ProjectIndexRequest,
    total_files: int,
    batch_count: int,
    batch_size: int,
    discovered_at: str,
    transport_metadata: Mapping[str, object],
    transport_event_data: Mapping[str, object],
) -> ProjectIndexWorkflowStart:
    """Build the initial persisted metadata for a project-index workflow.

    Queue transport identity is opaque to core: the runtime that owns the queue
    passes its durable ``transport`` metadata dict and any transport fields it
    wants merged into the attempt event (inserted between the discovery counts
    and the project identity to keep persisted event shapes stable).
    """
    counters = initial_project_index_counters(total_files)
    progress = project_index_progress_text(counters)
    payload = request.workflow_payload_metadata()
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
        "transport": dict(transport_metadata),
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
            **dict(transport_event_data),
            "project_id": request.project.project_id,
            "project_name": request.project.project_name,
            "project_permalink": request.project.project_permalink,
            "project_path": request.project.project_path,
        },
    )


def plan_project_index_workflow_start(
    *,
    request: ProjectIndexRequest,
    total_files: int,
    batch_count: int,
    batch_size: int,
    discovered_at: str,
    transport_metadata: Mapping[str, object],
    transport_event_data: Mapping[str, object],
) -> ProjectIndexWorkflowStartPlan:
    """Plan initial workflow metadata and immediate completion for empty projects."""
    workflow_start = build_project_index_workflow_start(
        request=request,
        total_files=total_files,
        batch_count=batch_count,
        batch_size=batch_size,
        discovered_at=discovered_at,
        transport_metadata=transport_metadata,
        transport_event_data=transport_event_data,
    )
    if total_files == 0:
        return ProjectIndexWorkflowStartComplete(
            workflow_start=workflow_start,
            completion_update=build_project_index_workflow_completion_update(
                metadata=workflow_start.metadata,
                counters=workflow_start.counters,
                progress=workflow_start.progress,
            ),
        )
    return ProjectIndexWorkflowStartRunning(workflow_start)


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
        return ProjectIndexWorkflowRecordComplete(
            progress_update=progress_update,
            completion_update=build_project_index_workflow_completion_update(
                metadata=progress_update.metadata,
                counters=counters,
                progress=progress_update.progress,
            ),
        )
    return ProjectIndexWorkflowRecordProgress(progress_update)


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
        return ProjectIndexWorkflowAlreadyRecorded()

    counters = batch_update.counters
    progress_update = build_project_index_workflow_progress_update(
        metadata=metadata,
        counters=counters,
        recorded_batch_indexes=batch_update.recorded_batch_indexes,
    )
    if batch_update.is_complete:
        return ProjectIndexWorkflowRecordComplete(
            progress_update=progress_update,
            completion_update=build_project_index_workflow_completion_update(
                metadata=progress_update.metadata,
                counters=counters,
                progress=progress_update.progress,
            ),
        )
    return ProjectIndexWorkflowRecordProgress(progress_update)


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
        return ProjectIndexStaleWorkflowKeepRunning(
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
    return ProjectIndexStaleWorkflowFail(
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
