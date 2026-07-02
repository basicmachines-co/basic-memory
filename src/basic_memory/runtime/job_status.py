"""Portable job-status response payloads."""

from __future__ import annotations

from dataclasses import dataclass

from basic_memory.runtime.workflows import (
    RuntimeJobStatus,
    RuntimeJobStatusType,
    RuntimeWorkflowCheckpoint,
    RuntimeWorkflowPhase,
    RuntimeWorkflowResult,
)

type RuntimeJobStatusResponseValue = RuntimeJobStatusType | str | dict[str, object] | None
type RuntimeJobStatusResponseDict = dict[str, RuntimeJobStatusResponseValue]


@dataclass(frozen=True, slots=True)
class RuntimeJobStatusResponsePayload:
    """Serialized runtime job status fields shared by REST and SSE adapters."""

    job_id: str
    tenant_id: str
    status: RuntimeJobStatusType
    result: RuntimeWorkflowResult | None = None
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    progress: str | None = None
    phase: RuntimeWorkflowPhase | None = None
    checkpoint: RuntimeWorkflowCheckpoint | None = None
    workflow_id: str | None = None
    index_job_id: str | None = None

    def as_dict(self) -> RuntimeJobStatusResponseDict:
        """Return the existing JSON-compatible job status payload shape."""
        return {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "result": dict(self.result) if self.result is not None else None,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "phase": self.phase,
            "checkpoint": dict(self.checkpoint) if self.checkpoint is not None else None,
            "workflow_id": self.workflow_id,
            "index_job_id": self.index_job_id,
        }


def runtime_job_status_response_payload(
    job_status: RuntimeJobStatus,
) -> RuntimeJobStatusResponsePayload:
    """Map an internal runtime job status to the stable public payload fields."""
    return RuntimeJobStatusResponsePayload(
        job_id=job_status.job_id,
        tenant_id=job_status.tenant_id or "unknown",
        status=job_status.status,
        result=job_status.result,
        error=job_status.error,
        created_at=job_status.created_at.isoformat() if job_status.created_at else None,
        started_at=job_status.started_at.isoformat() if job_status.started_at else None,
        finished_at=job_status.finished_at.isoformat() if job_status.finished_at else None,
        progress=job_status.progress,
        phase=job_status.phase,
        checkpoint=job_status.checkpoint,
        workflow_id=job_status.workflow_id,
        index_job_id=job_status.index_job_id,
    )
