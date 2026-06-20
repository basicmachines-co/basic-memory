"""Portable runtime queue identities."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from basic_memory.runtime.contracts import (
    JobEntrypoint,
    RuntimeJobDedupeKey,
    RuntimeJobRequest,
    TenantId,
    WorkflowId,
    runtime_job_request_from_source,
)


@dataclass(frozen=True, slots=True)
class RuntimeTenantWorkflowJobIdentity:
    """Tenant-scoped workflow identity for queue requests."""

    tenant_id: TenantId
    workflow_id: WorkflowId
    dedupe_prefix: RuntimeJobDedupeKey

    def dedupe_key(self) -> RuntimeJobDedupeKey:
        """Return the existing tenant workflow queue dedupe key."""
        return f"{self.dedupe_prefix}:{self.tenant_id}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the tenant workflow job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "workflow_id": str(self.workflow_id),
            }
        )
        return routing_headers

    def job_request(
        self,
        *,
        entrypoint: JobEntrypoint,
        payload: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        priority: int = 0,
        execute_after: timedelta | None = None,
    ) -> RuntimeJobRequest:
        """Build the runtime queue request for this tenant workflow identity."""
        return runtime_job_request_from_source(
            self,
            entrypoint=entrypoint,
            payload=payload,
            headers=headers,
            priority=priority,
            execute_after=execute_after,
        )
