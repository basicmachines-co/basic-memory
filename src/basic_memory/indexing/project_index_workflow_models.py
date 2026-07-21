"""Portable project-index workflow plans and persisted metadata models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Self

from basic_memory.indexing.progress import CheckpointModel
from basic_memory.indexing.project_index_progress import ProjectIndexCounters
from basic_memory.runtime.projects import ProjectRuntimeReference


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


# The workflow metadata document is validated with Pydantic on read (see
# project_index_progress). These models are the matching typed write side:
# field names and order define the persisted JSON shape, so builders dump
# them instead of mutating dict[str, object] by string key.
class ProjectIndexDiscoveryMetadata(CheckpointModel):
    """Fan-out discovery facts recorded when a project-index workflow starts."""

    total_files: int
    batch_count: int
    batch_size: int
    discovered_at: str


class ProjectIndexWorkflowStartMetadata(CheckpointModel):
    """Initial checkpoint metadata document for a project-index workflow."""

    phase: Literal["indexing"] = "indexing"
    progress: str
    payload: dict[str, object]
    discovery: ProjectIndexDiscoveryMetadata
    counters: dict[str, int]
    transport: dict[str, object]


class ProjectIndexWorkflowProgressMetadata(CheckpointModel):
    """Checkpoint metadata fields rewritten by one running progress update."""

    phase: Literal["indexing"] = "indexing"
    progress: str
    counters: dict[str, int]
    # None means "leave any previously recorded batches untouched"; the field
    # is dropped from the dump so per-file workflows never write the key.
    recorded_batches: list[int] | None = None


class ProjectIndexWorkflowCompletionMetadata(CheckpointModel):
    """Checkpoint metadata fields rewritten by terminal workflow success."""

    phase: Literal["completed"] = "completed"
    progress: str
    counters: dict[str, int]
    result: dict[str, int]


class ProjectIndexStaleDiagnostics(CheckpointModel):
    """Diagnostics recorded when project-index batch fan-out stalls."""

    reason: Literal["stale_project_index_batches"] = "stale_project_index_batches"
    missing_batches: list[int]
    recorded_batches: list[int]
    # Despite the historical key name, this is the "legacy rows lack a
    # batch_count" flag from ProjectIndexMissingBatches and persists as JSON
    # true/false.
    legacy_missing_batch_count: bool
    last_heartbeat_at: str
    stale_before: str


class ProjectIndexWorkflowFailureMetadata(CheckpointModel):
    """Checkpoint metadata fields rewritten by terminal workflow failure."""

    phase: Literal["failed"] = "failed"
    progress: str
    counters: dict[str, int]
    diagnostics: ProjectIndexStaleDiagnostics


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowAttemptEvent:
    """Attempt event payload for one project-index workflow start.

    Queue transport identity is opaque to core: the owning runtime's transport
    fields are spliced between the discovery counts and the project identity
    to keep persisted event shapes stable.
    """

    progress: str
    total_files: int
    batch_count: int
    batch_size: int
    transport_event_data: Mapping[str, object]
    project: ProjectRuntimeReference

    def to_event_data(self) -> dict[str, object]:
        """Serialize to the persisted attempt event shape."""
        return {
            "phase": "indexing",
            "progress": self.progress,
            "total_files": self.total_files,
            "batch_count": self.batch_count,
            "batch_size": self.batch_size,
            **dict(self.transport_event_data),
            "project_id": self.project.project_id,
            "project_name": self.project.project_name,
            "project_permalink": self.project.project_permalink,
            "project_path": self.project.project_path,
        }
