"""Stale project-index workflow recovery planning."""

from collections.abc import Mapping, Sequence

from basic_memory.indexing.project_index_progress import (
    ProjectIndexCounters,
    project_index_missing_batches_from_metadata,
)
from basic_memory.indexing.project_index_workflow_models import (
    ProjectIndexBatchJobActivity,
    ProjectIndexStaleDiagnostics,
    ProjectIndexStaleWorkflowFail,
    ProjectIndexStaleWorkflowKeepRunning,
    ProjectIndexStaleWorkflowPlan,
    ProjectIndexWorkflowFailureMetadata,
    ProjectIndexWorkflowFailureUpdate,
)
from basic_memory.indexing.project_index_workflow_updates import (
    build_project_index_batch_activity_update,
    require_project_index_workflow_counters,
)
from basic_memory.runtime.workflows import WorkflowId


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
    legacy_missing_batch_count: bool,
    last_heartbeat_at: str,
    stale_before: str,
) -> ProjectIndexWorkflowFailureUpdate:
    """Build terminal failure metadata for stale project-index batch fan-out."""
    missing_batches = list(missing_batch_indexes)
    if legacy_missing_batch_count:
        error_message = "Project index stalled with legacy batch metadata"
    else:
        error_message = f"Project index stalled with {len(missing_batches)} unreported batch(es)"
    progress = f"Project index stalled after {counters.processed}/{counters.total} files"
    failure_metadata = ProjectIndexWorkflowFailureMetadata(
        progress=progress,
        counters=counters.to_metadata(),
        diagnostics=ProjectIndexStaleDiagnostics(
            missing_batches=missing_batches,
            recorded_batches=list(recorded_batch_indexes),
            legacy_missing_batch_count=legacy_missing_batch_count,
            last_heartbeat_at=last_heartbeat_at,
            stale_before=stale_before,
        ),
    )
    failed_metadata = dict(metadata)
    failed_metadata.update(failure_metadata.model_dump())

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
            "diagnostics": failed_metadata["diagnostics"],
        },
    )
