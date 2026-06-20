"""Tests for portable runtime job request assembly."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from basic_memory.runtime import (
    RuntimeJobDedupeKey,
    RuntimeJobRequest,
    RuntimeTenantWorkflowJobIdentity,
    runtime_job_request_from_source,
)


@dataclass(frozen=True, slots=True)
class FakeRuntimeJobRequestSource:
    """Small request source proving the helper only needs the queue identity contract."""

    key: RuntimeJobDedupeKey

    def dedupe_key(self) -> RuntimeJobDedupeKey:
        return self.key

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        routing_headers = dict(headers or {})
        routing_headers["tenant_id"] = "tenant-1"
        routing_headers["project_id"] = "42"
        return routing_headers


def test_runtime_job_request_from_source_builds_queue_request() -> None:
    execute_after = timedelta(seconds=5)

    request = runtime_job_request_from_source(
        FakeRuntimeJobRequestSource("dedupe-key"),
        entrypoint="index_project",
        payload=b'{"project_id":42}',
        headers={"source": "test", "tenant_id": "caller"},
        priority=3,
        execute_after=execute_after,
    )

    assert request == RuntimeJobRequest(
        entrypoint="index_project",
        payload=b'{"project_id":42}',
        priority=3,
        execute_after=execute_after,
        dedupe_key="dedupe-key",
        headers={
            "source": "test",
            "tenant_id": "tenant-1",
            "project_id": "42",
        },
    )


def test_runtime_tenant_workflow_job_identity_builds_request_contract() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    identity = RuntimeTenantWorkflowJobIdentity(
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        dedupe_prefix="provision",
    )

    assert identity.dedupe_key() == f"provision:{tenant_id}"
    assert identity.routing_headers(headers={"source": "repair"}) == {
        "source": "repair",
        "tenant_id": str(tenant_id),
        "workflow_id": str(workflow_id),
    }
    assert identity.job_request(
        entrypoint="provision_tenant",
        payload=b'{"tenant_id":"demo"}',
        headers={"source": "repair"},
    ) == RuntimeJobRequest(
        entrypoint="provision_tenant",
        payload=b'{"tenant_id":"demo"}',
        dedupe_key=f"provision:{tenant_id}",
        headers={
            "source": "repair",
            "tenant_id": str(tenant_id),
            "workflow_id": str(workflow_id),
        },
    )
