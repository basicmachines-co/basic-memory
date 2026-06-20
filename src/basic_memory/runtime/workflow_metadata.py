"""Portable helpers for workflow metadata patches."""

from dataclasses import dataclass
from typing import Literal, Self

from basic_memory.runtime.contracts import (
    JobEntrypoint,
    RuntimeJobId,
    RuntimeWorkflowBroker,
    RuntimeWorkflowMetadataPatch,
)

type RuntimeWorkflowTransportState = Literal["running"]


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
