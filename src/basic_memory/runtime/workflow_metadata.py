"""Portable helpers for workflow metadata patches."""

from dataclasses import dataclass
from typing import Literal, Self

from basic_memory.runtime.jobs import JobEntrypoint, RuntimeJobId
from basic_memory.runtime.workflows import (
    RuntimeWorkflowBroker,
    RuntimeWorkflowFailureMetadata,
    RuntimeWorkflowMetadataPatch,
    RuntimeWorkflowProgress,
)

type RuntimeWorkflowTransportState = Literal["running"]
type RuntimeWorkflowQueueName = str
type RuntimeWorkflowType = str


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowAttemptTransportMetadata:
    """Transport metadata stored when a runtime workflow attempt starts."""

    broker: RuntimeWorkflowBroker
    entrypoint: JobEntrypoint
    pgq_job_id: RuntimeJobId | None
    state: RuntimeWorkflowTransportState = "running"

    @classmethod
    def pgq(cls, *, entrypoint: JobEntrypoint, pgq_job_id: RuntimeJobId | None) -> Self:
        """Build the existing PGQ transport metadata shape."""
        return cls(broker="pgq", entrypoint=entrypoint, pgq_job_id=pgq_job_id)

    def as_dict(self) -> dict[str, object]:
        """Serialize to the durable workflow transport metadata shape."""
        return {
            "broker": self.broker,
            "entrypoint": self.entrypoint,
            "pgq_job_id": self.pgq_job_id,
            "state": self.state,
        }

    def metadata_patch(
        self,
        *,
        extra: RuntimeWorkflowMetadataPatch | None = None,
    ) -> dict[str, object]:
        """Return a workflow metadata patch with optional top-level fields."""
        patch: dict[str, object] = {"transport": self.as_dict()}
        if extra is not None:
            for key, value in extra.items():
                patch[key] = value
        return patch


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowEnqueueFailureMetadata:
    """Workflow failure metadata for queue adapter enqueue failures."""

    queue_name: RuntimeWorkflowQueueName
    workflow_type: RuntimeWorkflowType
    raw_error_message: str
    progress: RuntimeWorkflowProgress = "enqueue failed"

    @classmethod
    def from_error(
        cls,
        *,
        queue_name: RuntimeWorkflowQueueName,
        workflow_type: RuntimeWorkflowType,
        error: BaseException | str,
    ) -> Self:
        """Build enqueue-failure metadata from a queue adapter exception."""
        return cls(
            queue_name=queue_name,
            workflow_type=workflow_type,
            raw_error_message=str(error),
        )

    @property
    def error_message(self) -> str:
        """Return the existing durable enqueue-failure message."""
        return f"Failed to enqueue {self.queue_name} {self.workflow_type}: {self.raw_error_message}"

    def failure_metadata(self) -> RuntimeWorkflowFailureMetadata:
        """Return the shared runtime failure metadata wrapper."""
        return RuntimeWorkflowFailureMetadata(
            error_message=self.error_message,
            progress=self.progress,
        )

    def workflow_metadata_patch(self) -> dict[str, object]:
        """Serialize to the existing durable enqueue-failure metadata patch."""
        return self.failure_metadata().workflow_metadata_patch()

    def failed_event_data(self) -> dict[str, object]:
        """Serialize to the existing enqueue-failure event payload."""
        return self.failure_metadata().failed_event_data()
