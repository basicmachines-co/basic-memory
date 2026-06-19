"""Portable project-index workflow progress state."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, StrictInt, StrictStr, ValidationError, model_validator

from basic_memory.indexing.progress import CheckpointModel
from basic_memory.runtime import (
    ProjectExternalId,
    ProjectName,
    ProjectPath,
    ProjectPermalink,
    RuntimeNoteChangeSource,
    RuntimeStorageFileIndexMode,
    TenantId,
    WorkflowId,
)

PROJECT_INDEX_PROGRESS_EVENT_INTERVAL = 50


class ProjectIndexFileOutcome(StrEnum):
    """Portable child file outcomes that update aggregate indexing counters."""

    processed = "processed"
    current = "current"
    missing = "missing"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class ProjectIndexCounters:
    """Aggregate file counters for a project indexing workflow."""

    total: int
    processed: int
    succeeded: int
    missing: int
    failed: int

    def to_metadata(self) -> dict[str, int]:
        """Return the JSON metadata shape stored in workflow checkpoints."""
        return {
            "total": self.total,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "missing": self.missing,
            "failed": self.failed,
        }


@dataclass(frozen=True, slots=True)
class ProjectIndexMissingBatches:
    """Batch accounting extracted from workflow checkpoint metadata."""

    missing_batch_indexes: list[int]
    recorded_batch_indexes: list[int]
    legacy_missing_batch_count: bool


@dataclass(frozen=True, slots=True)
class ProjectIndexBatchCounterUpdate:
    """Counter update result for one idempotent project-index batch."""

    counters: ProjectIndexCounters
    recorded_batch_indexes: list[int]
    already_recorded: bool
    all_batches_recorded: bool

    @property
    def is_complete(self) -> bool:
        """Return whether aggregate counters and batch structure are both complete."""
        return (
            not self.already_recorded
            and self.all_batches_recorded
            and self.counters.processed >= self.counters.total
        )


@dataclass(frozen=True, slots=True)
class ProjectIndexFileOutcomeSummary:
    """Per-batch summary counts for child file outcomes."""

    total_files: int
    processed_files: int
    missing_files: int
    failed_files: int


@dataclass(frozen=True, slots=True)
class ProjectIndexCompletion:
    """Workflow completion facts needed by runtime-side live update publishing."""

    tenant_id: TenantId
    project_id: str | None
    project_external_id: ProjectExternalId
    project_name: ProjectName | None
    project_permalink: ProjectPermalink | None
    project_path: ProjectPath | None
    workflow_id: WorkflowId
    progress: str
    counters: dict[str, int]


@dataclass(frozen=True, slots=True)
class ObservedObjectIndexCompletionContext:
    """Project context for one observed-object index completion update."""

    tenant_id: TenantId
    project_external_id: ProjectExternalId | None
    project_name: ProjectName | None
    project_path: ProjectPath
    mode: RuntimeStorageFileIndexMode
    workflow_id: WorkflowId | None = None


class ProjectIndexCompletedLiveUpdateType(StrEnum):
    """Project-level live-update events produced by project indexing."""

    index_completed = "index.completed"


@dataclass(frozen=True, slots=True)
class ProjectIndexCompletedLiveUpdatePlan:
    """Typed project-index completion update for runtime adapters to publish."""

    event_type: ProjectIndexCompletedLiveUpdateType
    tenant_id: TenantId
    source: RuntimeNoteChangeSource
    project_external_id: ProjectExternalId | None
    project_name: ProjectName | None
    workflow_id: WorkflowId | None
    cache_project_ids: tuple[str, ...] = ()


DEFAULT_PROJECT_INDEX_COMPLETED_LIVE_UPDATE_SOURCE: RuntimeNoteChangeSource = "worker"


def project_index_completion_cache_project_ids(
    *project_ids: str | None,
) -> tuple[str, ...]:
    """Return every project route id whose directory reads may be cached."""
    cache_project_ids: list[str] = []
    for project_id in project_ids:
        if project_id and project_id not in cache_project_ids:
            cache_project_ids.append(project_id)
    return tuple(cache_project_ids)


class ProjectIndexCountersState(CheckpointModel):
    """JSON payload for project-index aggregate counters."""

    total: StrictInt
    processed: StrictInt
    succeeded: StrictInt
    missing: StrictInt
    failed: StrictInt

    def to_counters(self) -> ProjectIndexCounters:
        """Convert validated JSON state into the immutable internal value."""
        return ProjectIndexCounters(
            total=self.total,
            processed=self.processed,
            succeeded=self.succeeded,
            missing=self.missing,
            failed=self.failed,
        )


class ProjectIndexDiscoveryState(CheckpointModel):
    """JSON payload describing project-index fan-out discovery."""

    total_files: StrictInt | None = None
    batch_count: StrictInt | None = None
    batch_size: StrictInt | None = None
    discovered_at: str | None = None

    @model_validator(mode="after")
    def require_batch_count_or_total_files(self) -> "ProjectIndexDiscoveryState":
        """Legacy rows may omit batch_count, but must still carry total_files."""
        if self.batch_count is None and self.total_files is None:
            raise ValueError("batch_count or total_files is required")
        return self


class ProjectIndexWorkflowProgressState(CheckpointModel):
    """JSON payload for project-index workflow metadata fields owned by core indexing."""

    discovery: ProjectIndexDiscoveryState
    counters: ProjectIndexCountersState | None = None
    recorded_batches: list[StrictInt] = Field(default_factory=list)


class ProjectIndexWorkflowPayloadState(CheckpointModel):
    """JSON payload for project identity stored with project-index workflows."""

    project_id: StrictInt | StrictStr | None = None
    project_external_id: StrictStr | None = None
    project_name: StrictStr | None = None
    project_permalink: StrictStr | None = None
    project_path: StrictStr | None = None


def initial_project_index_counters(total_files: int) -> ProjectIndexCounters:
    """Return empty aggregate counters for a newly discovered project index run."""
    return ProjectIndexCounters(
        total=total_files,
        processed=0,
        succeeded=0,
        missing=0,
        failed=0,
    )


def project_index_progress_text(counters: ProjectIndexCounters) -> str:
    """Format aggregate project-index progress for user-facing workflow status."""
    if counters.total == 0:
        return "No files found"

    parts = [
        f"Indexed {counters.processed}/{counters.total} files",
        f"{counters.succeeded} succeeded",
    ]
    if counters.missing:
        parts.append(f"{counters.missing} missing")
    if counters.failed:
        parts.append(f"{counters.failed} failed")
    return ", ".join(parts)


def should_emit_project_index_progress_event(
    counters: ProjectIndexCounters,
    *,
    event_interval: int = PROJECT_INDEX_PROGRESS_EVENT_INTERVAL,
) -> bool:
    """Return whether an aggregate workflow event should be emitted."""
    return (
        counters.processed == 1
        or counters.processed == counters.total
        or counters.processed % event_interval == 0
    )


def apply_project_index_file_outcome(
    counters: ProjectIndexCounters,
    outcome: ProjectIndexFileOutcome,
) -> ProjectIndexCounters:
    """Apply one child file outcome to immutable aggregate counters."""
    return apply_project_index_file_outcomes(counters, [outcome])


def apply_project_index_file_outcomes(
    counters: ProjectIndexCounters,
    outcomes: Sequence[ProjectIndexFileOutcome],
) -> ProjectIndexCounters:
    """Apply child file outcomes to immutable aggregate counters."""
    processed = counters.processed
    succeeded = counters.succeeded
    missing = counters.missing
    failed = counters.failed

    for outcome in outcomes:
        processed += 1
        if outcome in {ProjectIndexFileOutcome.processed, ProjectIndexFileOutcome.current}:
            succeeded += 1
        elif outcome == ProjectIndexFileOutcome.missing:
            missing += 1
        else:
            failed += 1

    return ProjectIndexCounters(
        total=counters.total,
        processed=processed,
        succeeded=succeeded,
        missing=missing,
        failed=failed,
    )


def summarize_project_index_file_outcomes(
    outcomes: Sequence[ProjectIndexFileOutcome],
) -> ProjectIndexFileOutcomeSummary:
    """Summarize child file outcomes for batch-level job results."""
    counters = apply_project_index_file_outcomes(
        initial_project_index_counters(total_files=len(outcomes)),
        outcomes,
    )
    return ProjectIndexFileOutcomeSummary(
        total_files=counters.total,
        processed_files=counters.succeeded,
        missing_files=counters.missing,
        failed_files=counters.failed,
    )


def apply_project_index_batch_outcomes(
    *,
    counters: ProjectIndexCounters,
    recorded_batch_indexes: Sequence[int],
    batch_index: int,
    batch_count: int,
    outcomes: Sequence[ProjectIndexFileOutcome],
) -> ProjectIndexBatchCounterUpdate:
    """Apply one batch's child file outcomes exactly once."""
    recorded = list(recorded_batch_indexes)
    if batch_index in recorded:
        return ProjectIndexBatchCounterUpdate(
            counters=counters,
            recorded_batch_indexes=recorded,
            already_recorded=True,
            all_batches_recorded=len(recorded) >= batch_count,
        )

    recorded.append(batch_index)
    updated_counters = apply_project_index_file_outcomes(counters, outcomes)
    return ProjectIndexBatchCounterUpdate(
        counters=updated_counters,
        recorded_batch_indexes=recorded,
        already_recorded=False,
        all_batches_recorded=len(recorded) >= batch_count,
    )


def project_index_counters_from_metadata(
    metadata: Mapping[str, object],
    *,
    workflow_id: object,
) -> ProjectIndexCounters:
    """Validate and read aggregate counters from workflow metadata."""
    try:
        counters = ProjectIndexCountersState.model_validate(metadata.get("counters"))
    except ValidationError as exc:
        raise RuntimeError(
            f"Project index workflow counters for {workflow_id} are invalid"
        ) from exc
    return counters.to_counters()


def project_index_batch_count_from_metadata(metadata: Mapping[str, object]) -> int | None:
    """Validate and read discovered batch count from workflow metadata."""
    state = project_index_progress_state_from_metadata(metadata, field_name="discovery")
    return state.discovery.batch_count


def project_index_recorded_batches_from_metadata(metadata: Mapping[str, object]) -> list[int]:
    """Validate and read batch indexes already applied to aggregate counters."""
    state = project_index_progress_state_from_metadata(metadata, field_name="recorded_batches")
    return sorted(state.recorded_batches)


def project_index_missing_batches_from_metadata(
    metadata: Mapping[str, object],
) -> ProjectIndexMissingBatches:
    """Return batch indexes that never reported back to the aggregate workflow."""
    state = project_index_progress_state_from_metadata(metadata, field_name="discovery")
    recorded_batches = sorted(state.recorded_batches)
    if state.discovery.batch_count is None:
        return ProjectIndexMissingBatches(
            missing_batch_indexes=[],
            recorded_batch_indexes=recorded_batches,
            legacy_missing_batch_count=True,
        )

    recorded_batch_set = set(recorded_batches)
    return ProjectIndexMissingBatches(
        missing_batch_indexes=[
            batch_index
            for batch_index in range(state.discovery.batch_count)
            if batch_index not in recorded_batch_set
        ],
        recorded_batch_indexes=recorded_batches,
        legacy_missing_batch_count=False,
    )


def project_index_progress_state_from_metadata(
    metadata: Mapping[str, object],
    *,
    field_name: str,
) -> ProjectIndexWorkflowProgressState:
    """Validate project-index checkpoint metadata at the workflow JSON boundary."""
    try:
        return ProjectIndexWorkflowProgressState.model_validate(metadata)
    except ValidationError as exc:
        raise RuntimeError(f"Project index workflow {field_name} metadata is invalid") from exc


def project_index_completion_from_metadata(
    *,
    tenant_id: TenantId | None,
    workflow_id: WorkflowId,
    metadata: Mapping[str, object],
    progress: str,
    counters: ProjectIndexCounters,
) -> ProjectIndexCompletion:
    """Build completion facts from validated workflow metadata."""
    payload = project_index_payload_state_from_metadata(metadata, workflow_id=workflow_id)
    if tenant_id is None:
        raise RuntimeError(f"Project index workflow tenant is missing: {workflow_id}")
    if payload.project_external_id is None:
        raise RuntimeError(f"Project index workflow project_external_id is missing: {workflow_id}")

    return ProjectIndexCompletion(
        tenant_id=tenant_id,
        project_id=str(payload.project_id) if payload.project_id is not None else None,
        project_external_id=payload.project_external_id,
        project_name=payload.project_name,
        project_permalink=payload.project_permalink,
        project_path=payload.project_path,
        workflow_id=workflow_id,
        progress=progress,
        counters=counters.to_metadata(),
    )


def plan_project_index_completed_live_update(
    completion: ProjectIndexCompletion | None,
) -> ProjectIndexCompletedLiveUpdatePlan | None:
    """Plan a project-level live update for a terminal project-index workflow."""
    if completion is None:
        return None

    return ProjectIndexCompletedLiveUpdatePlan(
        event_type=ProjectIndexCompletedLiveUpdateType.index_completed,
        tenant_id=completion.tenant_id,
        source=DEFAULT_PROJECT_INDEX_COMPLETED_LIVE_UPDATE_SOURCE,
        project_external_id=completion.project_external_id or None,
        project_name=completion.project_name,
        workflow_id=completion.workflow_id,
        cache_project_ids=project_index_completion_cache_project_ids(
            completion.project_external_id,
            completion.project_permalink,
        ),
    )


def plan_observed_object_index_completed_live_update(
    context: ObservedObjectIndexCompletionContext,
) -> ProjectIndexCompletedLiveUpdatePlan | None:
    """Plan graph freshness after one webhook-driven file index finishes."""
    if context.workflow_id is not None:
        return None
    if context.mode != RuntimeStorageFileIndexMode.observed_object:
        return None
    if not context.project_external_id or not context.project_name:
        return None

    return ProjectIndexCompletedLiveUpdatePlan(
        event_type=ProjectIndexCompletedLiveUpdateType.index_completed,
        tenant_id=context.tenant_id,
        source=DEFAULT_PROJECT_INDEX_COMPLETED_LIVE_UPDATE_SOURCE,
        project_external_id=context.project_external_id,
        project_name=context.project_name,
        workflow_id=None,
        cache_project_ids=project_index_completion_cache_project_ids(
            context.project_external_id,
            context.project_path,
        ),
    )


def project_index_payload_state_from_metadata(
    metadata: Mapping[str, object],
    *,
    workflow_id: object,
) -> ProjectIndexWorkflowPayloadState:
    """Validate project-index payload metadata at the workflow JSON boundary."""
    try:
        return ProjectIndexWorkflowPayloadState.model_validate(metadata.get("payload"))
    except ValidationError as exc:
        raise RuntimeError(f"Project index workflow payload is invalid: {workflow_id}") from exc
