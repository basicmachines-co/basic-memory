"""Tests for portable job-status response payload mapping."""

from datetime import UTC, datetime

from basic_memory.runtime import RuntimeJobStatus, runtime_job_status_response_payload


def test_runtime_job_status_response_payload_preserves_public_shape():
    """Runtime job statuses serialize to the existing REST/SSE payload fields."""
    created_at = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
    started_at = datetime(2026, 6, 19, 12, 1, tzinfo=UTC)
    finished_at = datetime(2026, 6, 19, 12, 2, tzinfo=UTC)

    payload = runtime_job_status_response_payload(
        RuntimeJobStatus(
            job_id="job-1",
            tenant_id="tenant-1",
            status="complete",
            result={"success": True},
            error=None,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            progress="Indexed Main",
            phase="completed",
            checkpoint={"files_processed": 12},
            workflow_id="workflow-1",
            index_job_id="index-workflow-1",
        )
    )

    assert payload.as_dict() == {
        "job_id": "job-1",
        "tenant_id": "tenant-1",
        "status": "complete",
        "result": {"success": True},
        "error": None,
        "created_at": "2026-06-19T12:00:00+00:00",
        "started_at": "2026-06-19T12:01:00+00:00",
        "finished_at": "2026-06-19T12:02:00+00:00",
        "progress": "Indexed Main",
        "phase": "completed",
        "checkpoint": {"files_processed": 12},
        "workflow_id": "workflow-1",
        "index_job_id": "index-workflow-1",
    }


def test_runtime_job_status_response_payload_uses_unknown_tenant_label():
    """Global/admin statuses keep the historical public tenant placeholder."""
    payload = runtime_job_status_response_payload(RuntimeJobStatus(job_id="job-1", status="queued"))

    assert payload.tenant_id == "unknown"
    assert payload.as_dict()["tenant_id"] == "unknown"
