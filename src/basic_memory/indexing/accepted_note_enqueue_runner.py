"""Portable post-commit enqueue orchestration for accepted note changes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from basic_memory.runtime import (
    ProjectId,
    RuntimeAcceptedNoteChange,
    RuntimeEntityId,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteMaterializationJobRequest,
    RuntimePendingNoteFileDelete,
    RuntimePendingNoteMaterialization,
    TenantId,
    plan_note_file_delete_job_request,
    plan_note_materialization_job_request,
)

type AcceptedNotePayloadSerializer[PayloadT] = Callable[[PayloadT], dict[str, object]]


@dataclass(frozen=True, slots=True)
class AcceptedNoteEnqueueResult:
    """Immediate response state after accepted-note follow-up enqueueing."""

    status_code: int
    payload: dict[str, object]


class AcceptedNoteMaterializationEnqueuer(Protocol):
    """Capability that enqueues a materialized-note write request."""

    async def enqueue_note_materialization(
        self,
        request: RuntimeNoteMaterializationJobRequest,
    ) -> None: ...


class AcceptedNoteFileDeleteEnqueuer(Protocol):
    """Capability that enqueues materialized-note file cleanup."""

    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None: ...


class AcceptedNoteMaterializationFailureMarker(Protocol):
    """Capability that records materialization enqueue failure in accepted note state."""

    async def mark_note_materialization_failed(
        self,
        *,
        project_id: ProjectId,
        entity_id: RuntimeEntityId,
        error_message: str,
    ) -> None: ...


async def enqueue_accepted_note_materialization[PayloadT](
    accepted: RuntimeAcceptedNoteChange[PayloadT],
    *,
    tenant_id: TenantId,
    payload_serializer: AcceptedNotePayloadSerializer[PayloadT],
    materialization_enqueuer: AcceptedNoteMaterializationEnqueuer,
    failure_marker: AcceptedNoteMaterializationFailureMarker,
) -> AcceptedNoteEnqueueResult:
    """Queue materialization for an already-committed accepted note write."""
    payload = payload_serializer(accepted.payload)
    materialization = require_accepted_note_materialization(accepted)

    try:
        await materialization_enqueuer.enqueue_note_materialization(
            plan_note_materialization_job_request(
                tenant_id=tenant_id,
                materialization=materialization,
            )
        )
        return AcceptedNoteEnqueueResult(status_code=accepted.status_code, payload=payload)
    except Exception as exc:
        await mark_failed_materialization_enqueue(
            failure_marker,
            materialization=materialization,
            error=exc,
        )
        return AcceptedNoteEnqueueResult(
            status_code=accepted.status_code,
            payload=note_materialization_enqueue_failed_payload(payload, error=exc),
        )


async def enqueue_accepted_note_write_jobs[PayloadT](
    accepted: RuntimeAcceptedNoteChange[PayloadT],
    *,
    tenant_id: TenantId,
    payload_serializer: AcceptedNotePayloadSerializer[PayloadT],
    materialization_enqueuer: AcceptedNoteMaterializationEnqueuer,
    failure_marker: AcceptedNoteMaterializationFailureMarker,
    file_delete_enqueuer: AcceptedNoteFileDeleteEnqueuer,
) -> AcceptedNoteEnqueueResult:
    """Queue writeback and any separate delete cleanup for an accepted note write."""
    result = await enqueue_accepted_note_materialization(
        accepted,
        tenant_id=tenant_id,
        payload_serializer=payload_serializer,
        materialization_enqueuer=materialization_enqueuer,
        failure_marker=failure_marker,
    )
    if accepted.file_delete is None:
        return result

    return await enqueue_accepted_note_file_delete_request(
        status_code=result.status_code,
        payload=result.payload,
        tenant_id=tenant_id,
        file_delete=accepted.file_delete,
        file_delete_enqueuer=file_delete_enqueuer,
    )


async def enqueue_accepted_note_file_delete[PayloadT](
    accepted: RuntimeAcceptedNoteChange[PayloadT],
    *,
    tenant_id: TenantId,
    payload_serializer: AcceptedNotePayloadSerializer[PayloadT],
    file_delete_enqueuer: AcceptedNoteFileDeleteEnqueuer,
) -> AcceptedNoteEnqueueResult:
    """Queue file cleanup for an already-committed accepted note delete."""
    return await enqueue_accepted_note_file_delete_request(
        status_code=accepted.status_code,
        payload=payload_serializer(accepted.payload),
        tenant_id=tenant_id,
        file_delete=require_accepted_note_file_delete(accepted),
        file_delete_enqueuer=file_delete_enqueuer,
    )


async def enqueue_accepted_note_file_delete_request(
    *,
    status_code: int,
    payload: dict[str, object],
    tenant_id: TenantId,
    file_delete: RuntimePendingNoteFileDelete,
    file_delete_enqueuer: AcceptedNoteFileDeleteEnqueuer,
) -> AcceptedNoteEnqueueResult:
    """Queue one accepted note file cleanup request and update response state on failure."""
    try:
        await file_delete_enqueuer.enqueue_note_file_delete(
            plan_note_file_delete_job_request(
                tenant_id=tenant_id,
                file_delete=file_delete,
            )
        )
        return AcceptedNoteEnqueueResult(status_code=status_code, payload=payload)
    except Exception as exc:
        return AcceptedNoteEnqueueResult(
            status_code=status_code,
            payload=note_file_delete_enqueue_failed_payload(payload, error=exc),
        )


async def mark_failed_materialization_enqueue(
    failure_marker: AcceptedNoteMaterializationFailureMarker,
    *,
    materialization: RuntimePendingNoteMaterialization,
    error: Exception,
) -> None:
    """Record enqueue failure while preserving both exceptions if bookkeeping fails."""
    try:
        await failure_marker.mark_note_materialization_failed(
            project_id=materialization.project_id,
            entity_id=materialization.entity_id,
            error_message=str(error),
        )
    except Exception as mark_exc:
        raise ExceptionGroup(
            "Failed to enqueue note materialization and mark the note as failed",
            [error, mark_exc],
        ) from error


def require_accepted_note_materialization[PayloadT](
    accepted: RuntimeAcceptedNoteChange[PayloadT],
) -> RuntimePendingNoteMaterialization:
    """Return accepted materialization work or fail for the wrong operation shape."""
    if accepted.materialization is None:
        raise RuntimeError("Accepted note change does not contain a materialization")
    return accepted.materialization


def require_accepted_note_file_delete[PayloadT](
    accepted: RuntimeAcceptedNoteChange[PayloadT],
) -> RuntimePendingNoteFileDelete:
    """Return accepted file cleanup work or fail for the wrong operation shape."""
    if accepted.file_delete is None:
        raise RuntimeError("Accepted note change does not contain a file delete")
    return accepted.file_delete


def note_materialization_enqueue_failed_payload(
    payload: dict[str, object],
    *,
    error: Exception,
) -> dict[str, object]:
    """Return the accepted-note payload state after materialization enqueue failure."""
    failed_payload = dict(payload)
    failed_payload["file_write_status"] = "failed"
    failed_payload["last_materialization_error"] = str(error)
    return failed_payload


def note_file_delete_enqueue_failed_payload(
    payload: dict[str, object],
    *,
    error: Exception,
) -> dict[str, object]:
    """Return the accepted-note payload state after file-delete enqueue failure."""
    failed_payload = dict(payload)
    failed_payload["file_delete_status"] = "failed"
    failed_payload["error"] = str(error)
    return failed_payload
