"""Tests for portable indexing worker payload boundaries."""

from uuid import UUID

from basic_memory.indexing import (
    IndexFileJobPayload,
    IndexFileObjectMetadataPayload,
    IndexFileRuntimeRequest,
)
from basic_memory.runtime import (
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
)


def test_index_file_job_payload_maps_object_metadata_to_runtime_request() -> None:
    """The Pydantic worker payload preserves observed storage metadata."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    payload = IndexFileJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        object_metadata=IndexFileObjectMetadataPayload(etag='"etag-1"', size=12),
    )

    assert payload.object_metadata is not None
    assert payload.object_metadata.to_runtime_observation() == RuntimeStorageObjectObservation(
        etag='"etag-1"',
        size=12,
    )
    assert payload.runtime_job_identity().dedupe_key() == (
        "index-file:11111111-1111-1111-1111-111111111111:101:notes/a.md:observed:etag-1:12"
    )
    assert payload.to_runtime_request() == IndexFileRuntimeRequest(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(etag='"etag-1"', size=12),
        index_embeddings=True,
        workflow_id=None,
    )


def test_index_file_job_payload_from_runtime_request_restores_payload() -> None:
    """A storage-neutral runtime request can be serialized for worker execution."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    runtime_request = IndexFileRuntimeRequest(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(etag='"etag-1"', size=12),
        index_embeddings=False,
        workflow_id=workflow_id,
    )

    assert IndexFileJobPayload.from_runtime_request(runtime_request) == IndexFileJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_metadata=IndexFileObjectMetadataPayload(etag='"etag-1"', size=12),
        index_embeddings=False,
        workflow_id=workflow_id,
    )
