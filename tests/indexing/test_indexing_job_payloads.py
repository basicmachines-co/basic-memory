"""Tests for portable indexing worker payload boundaries."""

from datetime import timedelta

from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexBatchJobRequest,
    EmbeddingIndexJobRequest,
    EmbeddingIndexTarget,
)
from basic_memory.indexing.index_file_runtime import IndexFileRuntimeRequest
from basic_memory.indexing.job_payloads import (
    DELETE_PROJECT_ENTRYPOINT,
    INDEX_EMBEDDINGS_BATCH_ENTRYPOINT,
    INDEX_EMBEDDINGS_ENTRYPOINT,
    INDEX_FILE_BATCH_ENTRYPOINT,
    INDEX_FILE_ENTRYPOINT,
    INDEX_PROJECT_ENTRYPOINT,
    RESOLVE_RELATIONS_ENTRYPOINT,
    EmbeddingIndexBatchJobPayload,
    EmbeddingIndexJobPayload,
    EmbeddingIndexTargetPayload,
    IndexFileBatchJobPayload,
    IndexFileJobPayload,
    IndexFileObjectMetadataPayload,
    ObservedIndexFilePayload,
    ProjectDeleteJobPayload,
    ProjectIndexJobPayload,
    ResolveRelationsJobPayload,
)
from basic_memory.indexing.relation_resolution import ResolveRelationsJobRequest
from basic_memory.runtime.jobs import (
    RuntimeIndexFileBatchJobRequest,
    RuntimeJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectDeleteJobRequest,
    RuntimeProjectIndexJobRequest,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
)
from basic_memory.runtime.projects import ProjectRuntimeReference


def test_index_file_job_payload_maps_object_metadata_to_runtime_request() -> None:
    """The Pydantic worker payload preserves observed storage metadata."""
    payload = IndexFileJobPayload(
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
        "index-file:101:notes/a.md:observed:etag-1:12"
    )
    assert payload.to_runtime_request() == IndexFileRuntimeRequest(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(etag='"etag-1"', size=12),
        index_embeddings=True,
    )


def test_index_file_job_payload_from_runtime_request_restores_payload() -> None:
    """A storage-neutral runtime request can be serialized for worker execution."""
    runtime_request = IndexFileRuntimeRequest(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(etag='"etag-1"', size=12),
        index_embeddings=False,
    )

    assert IndexFileJobPayload.from_runtime_request(runtime_request) == IndexFileJobPayload(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_metadata=IndexFileObjectMetadataPayload(etag='"etag-1"', size=12),
        index_embeddings=False,
    )


def test_index_file_runtime_request_exposes_queue_identity() -> None:
    """Index-file requests provide the generic runtime job source contract."""
    runtime_request = IndexFileRuntimeRequest(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(etag='"etag-1"', size=12),
    )

    assert runtime_request.dedupe_key() == "index-file:101:notes/a.md:observed:etag-1:12"
    assert runtime_request.routing_headers({"source": "test"}) == {
        "source": "test",
        "project_id": "101",
    }


def test_index_file_job_payload_builds_runtime_queue_request() -> None:
    """Index-file payloads build the concrete runtime job request shape."""
    payload = IndexFileJobPayload(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        object_metadata=IndexFileObjectMetadataPayload(etag='"etag-1"', size=12),
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_file",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key="index-file:101:notes/a.md:observed:etag-1:12",
        headers={
            "source": "test",
            "project_id": "101",
        },
    )


def test_index_file_batch_job_payload_round_trips_runtime_request() -> None:
    """File-batch jobs validate observed storage metadata at the worker boundary."""
    runtime_request = RuntimeIndexFileBatchJobRequest(
        project=ProjectRuntimeReference(
            project_id=101,
            project_external_id="project-main",
            project_path="main",
        ),
        batch_index=2,
        batch_count=5,
        file_paths=("notes/a.md",),
        observed_files=(RuntimeObservedIndexFile(path="notes/a.md", checksum="etag-a", size=123),),
        index_embeddings=False,
        force_full=True,
    )

    payload = IndexFileBatchJobPayload.from_runtime_request(runtime_request)

    assert payload == IndexFileBatchJobPayload(
        project_id=101,
        project_external_id="project-main",
        project_path="main",
        file_paths=["notes/a.md"],
        observed_files=[
            ObservedIndexFilePayload(path="notes/a.md", checksum="etag-a", size=123),
        ],
        batch_index=2,
        batch_count=5,
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
    payload = IndexFileBatchJobPayload(
        project_id=101,
        project_external_id="project-main",
        project_path="main",
        file_paths=["notes/a.md", "notes/b.md"],
        observed_files=[],
        batch_index=0,
        batch_count=1,
    )

    assert payload.targets() == [
        ObservedIndexFilePayload(path="notes/a.md"),
        ObservedIndexFilePayload(path="notes/b.md"),
    ]
    assert payload.target_paths() == ["notes/a.md", "notes/b.md"]


def test_index_file_batch_job_payload_builds_runtime_queue_request() -> None:
    """File-batch payloads build the concrete runtime job request shape."""
    payload = IndexFileBatchJobPayload(
        project_id=101,
        project_external_id="project-main",
        project_path="main",
        file_paths=["notes/a.md"],
        observed_files=[ObservedIndexFilePayload(path="notes/a.md", checksum="etag-a", size=123)],
        batch_index=2,
        batch_count=5,
        index_embeddings=False,
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_file_batch",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key="index-file-batch:101:2",
        headers={
            "source": "test",
            "project_id": "101",
            "project_external_id": "project-main",
            "project_path": "main",
        },
    )


def test_project_index_job_payload_round_trips_runtime_request() -> None:
    """Project-index jobs validate coordinator runtime requests at the worker boundary."""
    runtime_request = RuntimeProjectIndexJobRequest(
        project=ProjectRuntimeReference(
            project_id=101,
            project_external_id="project-main",
            project_name="Main",
            project_permalink="main",
            project_path="main",
        ),
        force_full=True,
        search=True,
        embeddings=False,
    )

    payload = ProjectIndexJobPayload.from_runtime_request(runtime_request)

    assert payload == ProjectIndexJobPayload(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_permalink="main",
        project_path="main",
        force_full=True,
        search=True,
        embeddings=False,
    )
    assert payload.project_reference() == runtime_request.project
    assert payload.to_runtime_request() == runtime_request


def test_indexing_entrypoints_export_cloud_queue_names() -> None:
    """The portable indexing contract owns cloud indexing queue names."""
    assert INDEX_FILE_ENTRYPOINT == "index_file"
    assert INDEX_FILE_BATCH_ENTRYPOINT == "index_file_batch"
    assert INDEX_PROJECT_ENTRYPOINT == "index_project"
    assert DELETE_PROJECT_ENTRYPOINT == "delete_project"
    assert INDEX_EMBEDDINGS_ENTRYPOINT == "index_embeddings"
    assert INDEX_EMBEDDINGS_BATCH_ENTRYPOINT == "index_embeddings_batch"
    assert RESOLVE_RELATIONS_ENTRYPOINT == "resolve_relations"


def test_project_index_job_payload_builds_runtime_queue_request() -> None:
    """Project-index payloads build the concrete runtime job request shape."""
    runtime_request = RuntimeProjectIndexJobRequest(
        project=ProjectRuntimeReference(
            project_id=101,
            project_external_id="project-main",
            project_name="Main",
            project_permalink="main",
            project_path="main",
        ),
        force_full=True,
        search=True,
        embeddings=False,
    )
    payload = ProjectIndexJobPayload.from_runtime_request(runtime_request)

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_project",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key="index-project:101",
        headers={
            "source": "test",
            "project_id": "101",
            "project_path": "main",
        },
    )


def test_project_delete_job_payload_round_trips_runtime_request() -> None:
    """Project-delete jobs validate cleanup runtime requests at the worker boundary."""
    runtime_request = RuntimeProjectDeleteJobRequest(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        delete_notes=False,
    )

    payload = ProjectDeleteJobPayload.from_runtime_request(runtime_request)

    assert payload == ProjectDeleteJobPayload(
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        delete_notes=False,
    )
    assert payload.to_runtime_request() == runtime_request


def test_project_delete_job_payload_builds_runtime_queue_request() -> None:
    """Project-delete payloads build the concrete runtime job request shape."""
    payload = ProjectDeleteJobPayload(
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
        dedupe_key="delete-project:101",
        headers={
            "source": "test",
            "project_id": "101",
        },
    )


def test_resolve_relations_job_payload_round_trips_runtime_request() -> None:
    """Relation-resolution jobs validate the core runtime request at the worker boundary."""
    runtime_request = ResolveRelationsJobRequest(
        project_id=101,
        project_path="main",
    )

    payload = ResolveRelationsJobPayload.from_runtime_request(runtime_request)

    assert payload == ResolveRelationsJobPayload(
        project_id=101,
        project_path="main",
    )
    assert payload.to_runtime_request() == runtime_request


def test_resolve_relations_job_payload_builds_runtime_queue_request() -> None:
    """Relation-resolution payloads build the concrete runtime job request shape."""
    payload = ResolveRelationsJobPayload(
        project_id=101,
        project_path="main",
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="resolve_relations",
        payload=payload.model_dump_json().encode("utf-8"),
        execute_after=timedelta(seconds=10),
        dedupe_key="resolve-relations:101",
        headers={
            "source": "test",
            "project_id": "101",
        },
    )


def test_embedding_index_job_payload_round_trips_runtime_request() -> None:
    """Embedding jobs validate single-entity runtime requests at the worker boundary."""
    runtime_request = EmbeddingIndexJobRequest(
        project_id=101,
        entity_id=42,
        entity_checksum="checksum-42",
    )

    payload = EmbeddingIndexJobPayload.from_runtime_request(runtime_request)

    assert payload == EmbeddingIndexJobPayload(
        project_id=101,
        entity_id=42,
        entity_checksum="checksum-42",
    )
    assert payload.to_runtime_request() == runtime_request


def test_embedding_index_job_payload_builds_runtime_queue_request() -> None:
    """Single-entity embedding payloads build the concrete runtime job request shape."""
    payload = EmbeddingIndexJobPayload(
        project_id=101,
        entity_id=42,
        entity_checksum="checksum-42",
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_embeddings",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key="index-embeddings:101:42:checksum-42",
        headers={
            "source": "test",
            "project_id": "101",
        },
    )


def test_embedding_index_batch_job_payload_round_trips_runtime_request() -> None:
    """Embedding batch jobs preserve entity target order at the worker boundary."""
    runtime_request = EmbeddingIndexBatchJobRequest(
        project_id=101,
        project_path="main",
        entities=(
            EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTarget(entity_id=43, entity_checksum="checksum-43"),
        ),
    )

    payload = EmbeddingIndexBatchJobPayload.from_runtime_request(runtime_request)

    assert payload == EmbeddingIndexBatchJobPayload(
        project_id=101,
        project_path="main",
        entities=[
            EmbeddingIndexTargetPayload(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTargetPayload(entity_id=43, entity_checksum="checksum-43"),
        ],
    )
    assert payload.targets() == list(runtime_request.entities)
    assert payload.to_runtime_request() == runtime_request


def test_embedding_index_batch_job_payload_builds_runtime_queue_request() -> None:
    """Embedding batch payloads build the concrete runtime job request shape."""
    payload = EmbeddingIndexBatchJobPayload(
        project_id=101,
        project_path="main",
        entities=[
            EmbeddingIndexTargetPayload(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTargetPayload(entity_id=43, entity_checksum="checksum-43"),
        ],
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="index_embeddings_batch",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key=payload.to_runtime_request().dedupe_key(),
        headers={
            "source": "test",
            "project_id": "101",
            "project_path": "main",
        },
    )
