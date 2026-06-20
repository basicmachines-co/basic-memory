"""Tests for portable workflow metadata helpers."""

from basic_memory.runtime import (
    RuntimeWorkflowAttemptTransportMetadata,
    RuntimeWorkflowEnqueueFailureMetadata,
    merge_runtime_workflow_metadata_patch,
)


def test_merge_runtime_workflow_metadata_patch_recurses_into_nested_mappings() -> None:
    metadata = {
        "phase": "queued",
        "transport": {
            "broker": "pgq",
            "entrypoint": "index_project",
            "pgq_job_id": "old-job",
        },
        "checkpoint": {
            "page": 1,
            "cursor": "abc",
        },
    }
    patch = {
        "phase": "running",
        "transport": {
            "pgq_job_id": "new-job",
            "state": "running",
        },
        "checkpoint": {
            "page": 2,
        },
    }

    assert merge_runtime_workflow_metadata_patch(metadata, patch) == {
        "phase": "running",
        "transport": {
            "broker": "pgq",
            "entrypoint": "index_project",
            "pgq_job_id": "new-job",
            "state": "running",
        },
        "checkpoint": {
            "page": 2,
            "cursor": "abc",
        },
    }


def test_merge_runtime_workflow_metadata_patch_replaces_non_mapping_values() -> None:
    metadata = {"checkpoint": {"cursor": "abc"}, "result": "legacy"}
    patch = {"checkpoint": None, "result": {"count": 2}}

    assert merge_runtime_workflow_metadata_patch(metadata, patch) == {
        "checkpoint": None,
        "result": {"count": 2},
    }


def test_runtime_workflow_attempt_transport_metadata_builds_running_patch() -> None:
    transport = RuntimeWorkflowAttemptTransportMetadata.pgq(
        entrypoint="provision_tenant",
        pgq_job_id="job-123",
    )

    assert transport.metadata_patch(extra={"image_tag": "registry.example/app:sha"}) == {
        "transport": {
            "broker": "pgq",
            "entrypoint": "provision_tenant",
            "pgq_job_id": "job-123",
            "state": "running",
        },
        "image_tag": "registry.example/app:sha",
    }


def test_runtime_workflow_enqueue_failure_metadata_builds_failure_shapes() -> None:
    failure = RuntimeWorkflowEnqueueFailureMetadata.from_error(
        queue_name="PGQ",
        workflow_type="tenant_provisioning",
        error=RuntimeError("queue offline"),
    )

    assert failure.error_message == ("Failed to enqueue PGQ tenant_provisioning: queue offline")
    assert failure.workflow_metadata_patch() == {
        "phase": "failed",
        "progress": "enqueue failed",
        "error_message": "Failed to enqueue PGQ tenant_provisioning: queue offline",
    }
    assert failure.failed_event_data() == {
        "phase": "failed",
        "progress": "enqueue failed",
        "error_message": "Failed to enqueue PGQ tenant_provisioning: queue offline",
    }
