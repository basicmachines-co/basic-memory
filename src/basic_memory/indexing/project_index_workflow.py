"""Portable project-index workflow metadata planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Self

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
