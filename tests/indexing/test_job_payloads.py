"""Tests for portable indexing worker payload boundaries."""

from datetime import timedelta
from uuid import UUID

from basic_memory import indexing as indexing_module
from basic_memory.indexing import (
    EmbeddingIndexBatchJobPayload,
    EmbeddingIndexBatchJobRequest,
    EmbeddingIndexJobPayload,
    EmbeddingIndexJobRequest,
    EmbeddingIndexTarget,
    EmbeddingIndexTargetPayload,
    IndexFileBatchJobPayload,
    IndexFileJobPayload,
    IndexFileObjectMetadataPayload,
    IndexFileRuntimeRequest,
    ObservedIndexFilePayload,
    ProjectDeleteJobPayload,
    ProjectIndexJobPayload,
    ResolveRelationsJobPayload,
    ResolveRelationsJobRequest,
)
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeIndexFileBatchJobRequest,
    RuntimeJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectDeleteJobRequest,
    RuntimeProjectIndexJobRequest,
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


def test_index_file_runtime_request_exposes_queue_identity() -> None:
    """Index-file requests provide the generic runtime job source contract."""
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
        workflow_id=workflow_id,
    )

    assert runtime_request.dedupe_key() == (
        "index-file:11111111-1111-1111-1111-111111111111:101:notes/a.md:observed:etag-1:12"
    )
    assert runtime_request.routing_headers({"source": "test"}) == {
        "source": "test",
        "tenant_id": str(tenant_id),
        "project_id": "101",
        "workflow_id": str(workflow_id),
    }


def test_index_file_job_payload_builds_runtime_queue_request() -> None:
    """Index-file payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    payload = IndexFileJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        object_metadata=IndexFileObjectMetadataPayload(etag='"etag-1"', size=12),
        workflow_id=workflow_id,
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_file",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key=(
            "index-file:11111111-1111-1111-1111-111111111111:101:notes/a.md:observed:etag-1:12"
        ),
        headers={
            "source": "test",
            "tenant_id": str(tenant_id),
            "project_id": "101",
            "workflow_id": str(workflow_id),
        },
    )


def test_index_file_batch_job_payload_round_trips_runtime_request() -> None:
    """File-batch jobs validate observed storage metadata at the worker boundary."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    runtime_request = RuntimeIndexFileBatchJobRequest(
        tenant_id=tenant_id,
        project=ProjectRuntimeReference(
            project_id=101,
            project_external_id="project-main",
            project_path="main",
        ),
        workflow_id=workflow_id,
        batch_index=2,
        batch_count=5,
        file_paths=("notes/a.md",),
        observed_files=(RuntimeObservedIndexFile(path="notes/a.md", checksum="etag-a", size=123),),
        index_embeddings=False,
        force_full=True,
    )

    payload = IndexFileBatchJobPayload.from_runtime_request(runtime_request)

    assert payload == IndexFileBatchJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_path="main",
        file_paths=["notes/a.md"],
        observed_files=[
            ObservedIndexFilePayload(path="notes/a.md", checksum="etag-a", size=123),
        ],
        batch_index=2,
        batch_count=5,
        workflow_id=workflow_id,
        index_embeddings=False,
        force_full=True,
    )
    assert payload.targets() == [
        ObservedIndexFilePayload(path="notes/a.md", checksum="etag-a", size=123),
    ]
    assert payload.target_paths() == ["notes/a.md"]
    assert payload.to_runtime_request() == runtime_request


def test_index_file_batch_job_payload_uses_file_paths_for_legacy_targets() -> None:
    """Legacy batch payloads still derive targets from file_paths."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    payload = IndexFileBatchJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_path="main",
        file_paths=["notes/a.md", "notes/b.md"],
        observed_files=[],
        batch_index=0,
        batch_count=1,
        workflow_id=workflow_id,
    )

    assert payload.targets() == [
        ObservedIndexFilePayload(path="notes/a.md"),
        ObservedIndexFilePayload(path="notes/b.md"),
    ]
    assert payload.target_paths() == ["notes/a.md", "notes/b.md"]


def test_index_file_batch_job_payload_builds_runtime_queue_request() -> None:
    """File-batch payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    payload = IndexFileBatchJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_path="main",
        file_paths=["notes/a.md"],
        observed_files=[ObservedIndexFilePayload(path="notes/a.md", checksum="etag-a", size=123)],
        batch_index=2,
        batch_count=5,
        workflow_id=workflow_id,
        index_embeddings=False,
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_file_batch",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key=(
            "index-file-batch:11111111-1111-1111-1111-111111111111:"
            "101:22222222-2222-2222-2222-222222222222:2"
        ),
        headers={
            "source": "test",
            "tenant_id": str(tenant_id),
            "project_id": "101",
            "project_external_id": "project-main",
            "project_path": "main",
            "workflow_id": str(workflow_id),
        },
    )


def test_project_index_job_payload_round_trips_runtime_request() -> None:
    """Project-index jobs validate coordinator runtime requests at the worker boundary."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    runtime_request = RuntimeProjectIndexJobRequest(
        tenant_id=tenant_id,
        project=ProjectRuntimeReference(
            project_id=101,
            project_external_id="project-main",
            project_name="Main",
            project_permalink="main",
            project_path="main",
        ),
        workflow_id=workflow_id,
        force_full=True,
        search=True,
        embeddings=False,
    )

    payload = ProjectIndexJobPayload.from_runtime_request(runtime_request)

    assert payload == ProjectIndexJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_permalink="main",
        project_path="main",
        workflow_id=workflow_id,
        force_full=True,
        search=True,
        embeddings=False,
    )
    assert payload.project_reference() == runtime_request.project
    assert payload.to_runtime_request() == runtime_request


def test_indexing_entrypoints_export_cloud_queue_names() -> None:
    """The portable indexing contract owns cloud indexing queue names."""
    assert indexing_module.INDEX_FILE_ENTRYPOINT == "index_file"
    assert indexing_module.INDEX_FILE_BATCH_ENTRYPOINT == "index_file_batch"
    assert indexing_module.INDEX_PROJECT_ENTRYPOINT == "index_project"
    assert indexing_module.DELETE_PROJECT_ENTRYPOINT == "delete_project"
    assert indexing_module.INDEX_EMBEDDINGS_ENTRYPOINT == "index_embeddings"
    assert indexing_module.INDEX_EMBEDDINGS_BATCH_ENTRYPOINT == "index_embeddings_batch"
    assert indexing_module.RESOLVE_RELATIONS_ENTRYPOINT == "resolve_relations"


def test_project_index_job_payload_builds_runtime_queue_request() -> None:
    """Project-index payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    runtime_request = RuntimeProjectIndexJobRequest(
        tenant_id=tenant_id,
        project=ProjectRuntimeReference(
            project_id=101,
            project_external_id="project-main",
            project_name="Main",
            project_permalink="main",
            project_path="main",
        ),
        workflow_id=workflow_id,
        force_full=True,
        search=True,
        embeddings=False,
    )
    payload = ProjectIndexJobPayload.from_runtime_request(runtime_request)

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_project",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key="index-project:11111111-1111-1111-1111-111111111111:101",
        headers={
            "source": "test",
            "tenant_id": str(tenant_id),
            "project_id": "101",
            "project_path": "main",
            "workflow_id": str(workflow_id),
        },
    )


def test_project_delete_job_payload_round_trips_runtime_request() -> None:
    """Project-delete jobs validate cleanup runtime requests at the worker boundary."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    runtime_request = RuntimeProjectDeleteJobRequest(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        delete_notes=False,
    )

    payload = ProjectDeleteJobPayload.from_runtime_request(runtime_request)

    assert payload == ProjectDeleteJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        delete_notes=False,
    )
    assert payload.to_runtime_request() == runtime_request


def test_project_delete_job_payload_builds_runtime_queue_request() -> None:
    """Project-delete payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    payload = ProjectDeleteJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        delete_notes=False,
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="delete_project",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key="delete-project:11111111-1111-1111-1111-111111111111:101",
        headers={
            "source": "test",
            "tenant_id": str(tenant_id),
            "project_id": "101",
        },
    )


def test_resolve_relations_job_payload_round_trips_runtime_request() -> None:
    """Relation-resolution jobs validate the core runtime request at the worker boundary."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    runtime_request = ResolveRelationsJobRequest(
        tenant_id=tenant_id,
        project_id=101,
        project_path="main",
    )

    payload = ResolveRelationsJobPayload.from_runtime_request(runtime_request)

    assert payload == ResolveRelationsJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_path="main",
    )
    assert payload.to_runtime_request() == runtime_request


def test_resolve_relations_job_payload_builds_runtime_queue_request() -> None:
    """Relation-resolution payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    payload = ResolveRelationsJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_path="main",
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="resolve_relations",
        payload=payload.model_dump_json().encode("utf-8"),
        execute_after=timedelta(seconds=10),
        dedupe_key="resolve-relations:11111111-1111-1111-1111-111111111111:101",
        headers={
            "source": "test",
            "tenant_id": str(tenant_id),
            "project_id": "101",
        },
    )


def test_embedding_index_job_payload_round_trips_runtime_request() -> None:
    """Embedding jobs validate single-entity runtime requests at the worker boundary."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    runtime_request = EmbeddingIndexJobRequest(
        tenant_id=tenant_id,
        project_id=101,
        entity_id=42,
        entity_checksum="checksum-42",
    )

    payload = EmbeddingIndexJobPayload.from_runtime_request(runtime_request)

    assert payload == EmbeddingIndexJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        entity_id=42,
        entity_checksum="checksum-42",
    )
    assert payload.to_runtime_request() == runtime_request


def test_embedding_index_job_payload_builds_runtime_queue_request() -> None:
    """Single-entity embedding payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    payload = EmbeddingIndexJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        entity_id=42,
        entity_checksum="checksum-42",
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_embeddings",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key=("index-embeddings:11111111-1111-1111-1111-111111111111:101:42:checksum-42"),
        headers={
            "source": "test",
            "tenant_id": str(tenant_id),
            "project_id": "101",
        },
    )


def test_embedding_index_batch_job_payload_round_trips_runtime_request() -> None:
    """Embedding batch jobs preserve entity target order at the worker boundary."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    runtime_request = EmbeddingIndexBatchJobRequest(
        tenant_id=tenant_id,
        project_id=101,
        project_path="main",
        workflow_id=workflow_id,
        entities=(
            EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTarget(entity_id=43, entity_checksum="checksum-43"),
        ),
    )

    payload = EmbeddingIndexBatchJobPayload.from_runtime_request(runtime_request)

    assert payload == EmbeddingIndexBatchJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_path="main",
        entities=[
            EmbeddingIndexTargetPayload(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTargetPayload(entity_id=43, entity_checksum="checksum-43"),
        ],
        workflow_id=workflow_id,
    )
    assert payload.targets() == list(runtime_request.entities)
    assert payload.to_runtime_request() == runtime_request


def test_embedding_index_batch_job_payload_builds_runtime_queue_request() -> None:
    """Embedding batch payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    payload = EmbeddingIndexBatchJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        project_path="main",
        entities=[
            EmbeddingIndexTargetPayload(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTargetPayload(entity_id=43, entity_checksum="checksum-43"),
        ],
        workflow_id=workflow_id,
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_embeddings_batch",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key=payload.to_runtime_request().dedupe_key(),
        headers={
            "source": "test",
            "tenant_id": str(tenant_id),
            "project_id": "101",
            "project_path": "main",
            "workflow_id": str(workflow_id),
        },
    )
