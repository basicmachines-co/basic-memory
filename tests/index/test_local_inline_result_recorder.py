"""Follow-up behavior for the local inline storage-event result recorder.

Covers #1016: the watcher/inline path must vector-embed newly indexed files
(when embeddings are enabled), not just refresh full-text search and relations.
"""

from typing import cast

from basic_memory.index.local_dependencies import LocalIndexSearchService
from basic_memory.index.local_runtime import LocalInlineStorageEventResultRecorder
from basic_memory.indexing.models import IndexFileJobResult, IndexFileJobStatus
from basic_memory.indexing.project_index_maintenance import ProjectIndexMovedEntitySearchRefresher
from basic_memory.indexing.relation_resolution import RelationResolutionRuntime
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import (
    RuntimeStorageEventOperation,
    RuntimeStorageEventOperationKind,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
)


def _project() -> ProjectRuntimeReference:
    return ProjectRuntimeReference(
        project_id=101,
        project_external_id="project-main",
        project_path="main",
        project_name="Main",
    )


def _operation() -> RuntimeStorageEventOperation:
    return RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.index_file,
        storage_event=StorageEventPayload(
            event_name="OBJECT_CREATED_PUT",
            event_time="2026-06-20T14:00:00Z",
            object_version=StorageObjectVersion(
                identity=StorageObjectIdentity(
                    bucket_name="local-filesystem", key="main/notes/a.md"
                ),
                etag="etag-a",
                size=12,
            ),
        ),
        relative_path="notes/a.md",
    )


class StubSearchService:
    """Records the entity ids handed to the inline embedding refresh."""

    def __init__(self) -> None:
        self.batches: list[list[int]] = []

    async def sync_entity_vectors_batch(self, entity_ids, progress_callback=None):
        self.batches.append(list(entity_ids))
        return None


class StubRelationRuntime:
    """Counts project relation resolution passes triggered by the recorder."""

    def __init__(self) -> None:
        self.resolve_calls = 0

    async def count_unresolved_relations(self) -> int:
        return 0

    async def resolve_relations(self, entity_id: int | None = None) -> set[int]:
        self.resolve_calls += 1
        return set()


def _recorder(
    search: StubSearchService,
    relations: StubRelationRuntime,
    *,
    index_embeddings: bool,
) -> LocalInlineStorageEventResultRecorder:
    return LocalInlineStorageEventResultRecorder(
        project=_project(),
        search_service=cast(LocalIndexSearchService, search),
        relation_cleanup_search_refresher=cast(ProjectIndexMovedEntitySearchRefresher, object()),
        relation_runtime=cast(RelationResolutionRuntime, relations),
        index_embeddings=index_embeddings,
    )


def _result(status: IndexFileJobStatus, entity_id: int | None) -> IndexFileJobResult:
    return IndexFileJobResult(status=status, reason="file indexed", entity_id=entity_id)


async def test_processed_file_embeds_and_resolves_when_enabled() -> None:
    search = StubSearchService()
    relations = StubRelationRuntime()
    recorder = _recorder(search, relations, index_embeddings=True)

    await recorder.index_file_completed(_operation(), _result(IndexFileJobStatus.processed, 42))

    assert search.batches == [[42]]
    assert relations.resolve_calls == 1


async def test_processed_file_skips_embedding_when_disabled() -> None:
    search = StubSearchService()
    relations = StubRelationRuntime()
    recorder = _recorder(search, relations, index_embeddings=False)

    await recorder.index_file_completed(_operation(), _result(IndexFileJobStatus.processed, 42))

    # Relation repair still runs; embedding is gated off.
    assert search.batches == []
    assert relations.resolve_calls == 1


async def test_unchanged_file_runs_no_followups() -> None:
    search = StubSearchService()
    relations = StubRelationRuntime()
    recorder = _recorder(search, relations, index_embeddings=True)

    await recorder.index_file_completed(_operation(), _result(IndexFileJobStatus.current, 42))

    assert search.batches == []
    assert relations.resolve_calls == 0
