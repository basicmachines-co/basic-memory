"""Portable workflow status and metadata contracts for Basic Memory runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from basic_memory.runtime.jobs import JobEntrypoint, RuntimeJobId, WorkflowId

type RuntimeWorkflowBroker = str
type RuntimeWorkflowCheckpoint = Mapping[str, object]
type RuntimeWorkflowMetadata = Mapping[str, object]
type RuntimeWorkflowMetadataPatch = Mapping[str, object]
type RuntimeWorkflowStatus = str
type RuntimeWorkflowPhase = str
type RuntimeWorkflowProgress = str
type RuntimeWorkflowResult = Mapping[str, object]
type RuntimeJobStatusType = Literal[
    "queued",
    "in_progress",
    "complete",
    "failed",
    "deferred",
    "not_found",
    "unknown",
    "cancelled",
]

WORKFLOW_EVENT_TEXT_MAX_CHARS = 4096
RUNTIME_ACTIVE_WORKFLOW_STATUSES: frozenset[RuntimeWorkflowStatus] = frozenset(
    {"queued", "running"}
)
RUNTIME_TERMINAL_WORKFLOW_STATUSES: frozenset[RuntimeWorkflowStatus] = frozenset(
    {"completed", "failed", "cancelled"}
)


def runtime_job_status_from_workflow_status(
    workflow_status: RuntimeWorkflowStatus,
) -> RuntimeJobStatusType:
    """Translate durable workflow states to the portable job-status vocabulary."""
    match workflow_status:
        case "queued":
            return "queued"
        case "running":
            return "in_progress"
        case "completed":
            return "complete"
        case "failed":
            return "failed"
        case "cancelled":
            return "cancelled"
        case _:
            return "unknown"


def parse_runtime_workflow_id(job_id: str) -> WorkflowId | None:
    """Best-effort workflow-id parsing for durable runtime status lookups."""
    try:
        return UUID(job_id)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowTransport:
    """Queue transport identity stored on workflow metadata."""

    broker: RuntimeWorkflowBroker
    entrypoint: JobEntrypoint

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize transport identity for existing workflow metadata contracts."""
        return {
            "broker": self.broker,
            "entrypoint": self.entrypoint,
        }


@dataclass(frozen=True, slots=True)
class RuntimeQueuedWorkflowMetadata:
    """Workflow metadata and queued event data for one runtime job handoff."""

    workflow_id: WorkflowId
    progress: RuntimeWorkflowProgress
    payload: Mapping[str, object]
    transport: RuntimeWorkflowTransport
    phase: RuntimeWorkflowPhase = "queued"

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize to the existing durable workflow metadata shape."""
        return {
            "job_id": str(self.workflow_id),
            "phase": self.phase,
            "progress": self.progress,
            "payload": dict(self.payload),
            "transport": self.transport.workflow_metadata(),
        }

    def queued_event_data(self, *, logical_key: str) -> dict[str, object]:
        """Serialize to the existing queued workflow event payload shape."""
        return {
            "logical_key": logical_key,
            "entrypoint": self.transport.entrypoint,
            "phase": self.phase,
            "progress": self.progress,
            **dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowMetadataView:
    """Typed read view over durable workflow metadata."""

    metadata: RuntimeWorkflowMetadata

    @classmethod
    def from_metadata(cls, metadata: RuntimeWorkflowMetadata | None) -> Self:
        """Build a read view from optional persisted workflow metadata."""
        return cls(metadata=dict(metadata or {}))

    @property
    def phase(self) -> RuntimeWorkflowPhase | None:
        """Return the latest machine-readable workflow phase."""
        phase = self.metadata.get("phase")
        return phase if isinstance(phase, str) else None

    @property
    def progress(self) -> RuntimeWorkflowProgress | None:
        """Return human-readable workflow progress, falling back to phase."""
        progress = self.metadata.get("progress")
        if isinstance(progress, str) and progress:
            return progress
        return self.phase

    @property
    def checkpoint(self) -> dict[str, object] | None:
        """Return copied checkpoint metadata for resumable workflow jobs."""
        checkpoint = self.metadata.get("checkpoint")
        return runtime_workflow_metadata_dict_value(checkpoint, field_name="checkpoint")

    @property
    def result(self) -> dict[str, object] | None:
        """Return copied structured result data from workflow metadata."""
        result = self.metadata.get("result")
        return runtime_workflow_metadata_dict_value(result, field_name="result")


def truncate_runtime_workflow_text(
    value: str,
    *,
    max_chars: int = WORKFLOW_EVENT_TEXT_MAX_CHARS,
) -> str:
    """Return a stable workflow text preview for durable metadata and event streams."""
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")

    if len(value) <= max_chars:
        return value

    omitted_chars = len(value) - max_chars
    suffix = f"... [truncated {omitted_chars} chars]"
    available_chars = max(max_chars - len(suffix), 0)
    return f"{value[:available_chars]}{suffix}"


def merge_runtime_workflow_metadata_patch(
    base: Mapping[str, object],
    metadata_patch: RuntimeWorkflowMetadataPatch | None,
) -> dict[str, object]:
    """Recursively merge adapter-specific workflow metadata patches."""
    patch = dict(base)
    if metadata_patch is not None:
        for key, value in metadata_patch.items():
            current = patch.get(key)
            current_mapping = runtime_workflow_metadata_mapping_value(current, field_name=key)
            value_mapping = runtime_workflow_metadata_mapping_value(value, field_name=key)
            if current_mapping is not None and value_mapping is not None:
                patch[key] = merge_runtime_workflow_metadata_patch(
                    current_mapping,
                    value_mapping,
                )
                continue
            patch[key] = value
    return patch


def runtime_workflow_metadata_mapping_value(
    value: object,
    *,
    field_name: str,
) -> dict[str, object] | None:
    """Return a copied workflow metadata mapping value with string keys."""
    if not isinstance(value, Mapping):
        return None

    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"Workflow {field_name} metadata keys must be strings")
        copied[key] = item
    return copied


def runtime_workflow_metadata_dict_value(
    value: object,
    *,
    field_name: str,
) -> dict[str, object] | None:
    """Return a copied workflow metadata object value with string keys."""
    if not isinstance(value, dict):
        return None

    return runtime_workflow_metadata_mapping_value(value, field_name=field_name)


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowAttemptMetadata:
    """Workflow metadata and event data for a started runtime attempt."""

    progress: RuntimeWorkflowProgress
    metadata_patch: RuntimeWorkflowMetadataPatch | None = None
    phase: RuntimeWorkflowPhase = "running"

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable attempt metadata patch shape."""
        return merge_runtime_workflow_metadata_patch(
            {
                "phase": self.phase,
                "progress": self.progress,
            },
            self.metadata_patch,
        )

    def attempt_started_event_data(
        self,
        *,
        attempt_number: int,
        pgq_job_id: RuntimeJobId | None,
    ) -> dict[str, object]:
        """Serialize to the existing attempt-started event payload shape."""
        return {
            "attempt_number": attempt_number,
            "pgq_job_id": pgq_job_id,
            "phase": self.phase,
            "progress": self.progress,
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowProgressMetadata:
    """Workflow metadata and event data for an in-flight progress update."""

    progress: RuntimeWorkflowProgress
    phase: RuntimeWorkflowPhase | None = None
    metadata_patch: RuntimeWorkflowMetadataPatch | None = None

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable progress metadata patch shape."""
        base: dict[str, object] = {"progress": self.progress}
        if self.phase is not None:
            base["phase"] = self.phase
        return merge_runtime_workflow_metadata_patch(base, self.metadata_patch)

    def progress_event_data(self) -> dict[str, object]:
        """Serialize to the existing progress event payload shape."""
        return {
            "phase": self.phase,
            "progress": self.progress,
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowCompletionMetadata:
    """Workflow metadata and event data for a completed runtime workflow."""

    result: RuntimeWorkflowResult | None = None
    metadata_patch: RuntimeWorkflowMetadataPatch | None = None
    progress: RuntimeWorkflowProgress = "completed"

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable completion metadata patch shape."""
        base: dict[str, object] = {
            "phase": "completed",
            "progress": self.progress,
        }
        if self.result is not None:
            base["result"] = dict(self.result)
        return merge_runtime_workflow_metadata_patch(base, self.metadata_patch)

    def completed_event_data(self) -> dict[str, object]:
        """Serialize to the existing completed event payload shape."""
        return {
            "phase": "completed",
            "progress": self.progress,
            "result": dict(self.result) if self.result is not None else None,
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowFailureMetadata:
    """Workflow metadata and event data for a failed runtime workflow."""

    error_message: str
    progress: RuntimeWorkflowProgress = "failed"
    metadata_patch: RuntimeWorkflowMetadataPatch | None = None

    @property
    def error_preview(self) -> str:
        """Return the stored/evented error preview without exposing provider details."""
        return truncate_runtime_workflow_text(self.error_message)

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable failure metadata patch shape."""
        return merge_runtime_workflow_metadata_patch(
            {
                "phase": "failed",
                "progress": self.progress,
                "error_message": self.error_preview,
            },
            self.metadata_patch,
        )

    def failed_event_data(self) -> dict[str, object]:
        """Serialize to the existing failed event payload shape."""
        return {
            "phase": "failed",
            "progress": self.progress,
            "error_message": self.error_preview,
        }


@dataclass(frozen=True, slots=True)
class RuntimeJobStatus:
    """Unified runtime job status and result information."""

    job_id: str
    status: RuntimeJobStatusType
    job_type: str | None = None
    tenant_id: str | None = None
    workflow_id: str | None = None
    image_tag: str | None = None
    error: str | None = None
    progress: str | None = None
    phase: RuntimeWorkflowPhase | None = None
    checkpoint: RuntimeWorkflowCheckpoint | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: RuntimeWorkflowResult | None = None
    index_job_id: str | None = None
