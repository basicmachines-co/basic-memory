"""Portable project-index workflow progress state."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, StrictInt, ValidationError, model_validator

from basic_memory.indexing.progress import CheckpointModel

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
