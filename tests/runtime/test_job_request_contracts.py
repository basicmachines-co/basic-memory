"""Tests for portable runtime job request assembly."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from basic_memory.runtime import (
    RuntimeJobDedupeKey,
    RuntimeJobRequest,
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
