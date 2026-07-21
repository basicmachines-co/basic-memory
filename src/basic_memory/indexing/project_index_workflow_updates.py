"""Project-index workflow start, progress, and child-result planning."""

from collections.abc import Mapping, Sequence

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
    project_index_progress_text,
    project_index_recorded_batches_from_metadata,
    should_emit_project_index_progress_event,
)
from basic_memory.indexing.project_index_workflow_models import (
    ProjectIndexBatchJobActivity,
    ProjectIndexBatchJobActivityUpdate,
    ProjectIndexDiscoveryMetadata,
    ProjectIndexWorkflowAlreadyRecorded,
    ProjectIndexWorkflowAttemptEvent,
    ProjectIndexWorkflowCompletionMetadata,
    ProjectIndexWorkflowCompletionUpdate,
    ProjectIndexWorkflowProgressMetadata,
    ProjectIndexWorkflowProgressUpdate,
    ProjectIndexWorkflowRecordComplete,
    ProjectIndexWorkflowRecordPlan,
    ProjectIndexWorkflowRecordProgress,
    ProjectIndexWorkflowStart,
    ProjectIndexWorkflowStartComplete,
    ProjectIndexWorkflowStartMetadata,
    ProjectIndexWorkflowStartPlan,
    ProjectIndexWorkflowStartRunning,
)
from basic_memory.runtime.workflows import WorkflowId


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
    wants merged into the attempt event (see ProjectIndexWorkflowAttemptEvent).
    """
    counters = initial_project_index_counters(total_files)
    progress = project_index_progress_text(counters)
    start_metadata = ProjectIndexWorkflowStartMetadata(
        progress=progress,
        payload=request.workflow_payload_metadata(),
        discovery=ProjectIndexDiscoveryMetadata(
            total_files=total_files,
            batch_count=batch_count,
            batch_size=batch_size,
            discovered_at=discovered_at,
        ),
        counters=counters.to_metadata(),
        transport=dict(transport_metadata),
    )
    attempt_event = ProjectIndexWorkflowAttemptEvent(
        progress=progress,
        total_files=total_files,
        batch_count=batch_count,
        batch_size=batch_size,
        transport_event_data=transport_event_data,
        project=request.project,
    )
    return ProjectIndexWorkflowStart(
        counters=counters,
        progress=progress,
        metadata=start_metadata.model_dump(),
        attempt_event_data=attempt_event.to_event_data(),
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
    progress_metadata = ProjectIndexWorkflowProgressMetadata(
        progress=progress,
        counters=counters_metadata,
        recorded_batches=(
            list(recorded_batch_indexes) if recorded_batch_indexes is not None else None
        ),
    )
    updated_metadata = dict(metadata)
    updated_metadata.update(progress_metadata.model_dump(exclude_none=True))

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
    completion_metadata = ProjectIndexWorkflowCompletionMetadata(
        progress=progress,
        counters=counters_metadata,
        result=counters_metadata,
    )
    completed_metadata = dict(metadata)
    completed_metadata.update(completion_metadata.model_dump())

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
