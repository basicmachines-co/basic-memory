"""First-party Milvus semantic vector index contract tests."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator, Sequence
from typing import override, Any

import pytest

pytest.importorskip("pymilvus", reason="install basic-memory[milvus] to test Milvus")

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.milvus_config import MilvusSettings
from basic_memory.repository.milvus_repository import (
    MilvusStoredMatch,
    MilvusStoredRecord,
)
from basic_memory.repository.milvus_index import (
    MilvusVectorIndex,
    collection_name,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_vector_index import (
    SemanticVectorIndex,
    SemanticVectorIndexReconciler,
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorRecord,
)
from basic_memory.repository.semantic_vector_index_factory import (
    create_semantic_vector_index,
)

PROJECT = 42
OTHER_PROJECT = 43
PROJECTS = ProjectScope.single(PROJECT)
QUERY = [1.0, 0.0, 0.0]


class FakeRepository:
    """In-memory recorder for the blocking Milvus repository."""

    def __init__(
        self,
        dimensions: int | None = None,
        *,
        create_result: bool = True,
        race_dimensions: int | None = None,
    ) -> None:
        self.dimensions = dimensions
        self.create_result = create_result
        self.race_dimensions = race_dimensions
        self.created: list[tuple[str, int]] = []
        self.loaded: list[str] = []
        self.upserts: list[tuple[str, list[MilvusStoredRecord]]] = []
        self.record_deletes: list[tuple[str, list[tuple[str, str]]]] = []
        self.entity_deletes: list[tuple[str, int]] = []
        self.ids: list[str] = []
        self.id_deletes: list[tuple[str, list[str]]] = []
        self.matches: list[MilvusStoredMatch] = []
        # Per-collection answers; ``matches`` is the answer for any collection not listed.
        self.matches_by_collection: dict[str, list[MilvusStoredMatch]] = {}
        self.searches: list[tuple[str, list[float], int]] = []
        self.closed = 0

    def collection_dimensions(self, collection_name: str) -> int | None:
        assert collection_name
        return self.dimensions

    def load_collection(self, collection_name: str) -> None:
        self.loaded.append(collection_name)

    def create_collection(self, collection_name: str, dimensions: int) -> bool:
        self.created.append((collection_name, dimensions))
        self.dimensions = dimensions if self.create_result else self.race_dimensions
        return self.create_result

    def upsert(
        self,
        collection_name: str,
        records: Sequence[MilvusStoredRecord],
    ) -> None:
        self.upserts.append((collection_name, list(records)))

    def delete_records(
        self,
        collection_name: str,
        records: Sequence[tuple[str, str]],
    ) -> None:
        self.record_deletes.append((collection_name, list(records)))

    def delete_entity(self, collection_name: str, entity_id: int) -> None:
        self.entity_deletes.append((collection_name, entity_id))

    def iter_ids(self, collection_name: str) -> Iterator[str]:
        assert collection_name
        return iter(self.ids)

    def delete_ids(self, collection_name: str, record_ids: Sequence[str]) -> None:
        self.id_deletes.append((collection_name, list(record_ids)))

    def search(
        self,
        collection_name: str,
        query: Sequence[float],
        limit: int,
    ) -> list[MilvusStoredMatch]:
        self.searches.append((collection_name, list(query), limit))
        return self.matches_by_collection.get(collection_name, self.matches)

    def close(self) -> None:
        self.closed += 1


class BlockingMutationRepository(FakeRepository):
    """Hold external mutations until a cancellation assertion releases them."""

    def __init__(self, dimensions: int) -> None:
        super().__init__(dimensions=dimensions)
        self.mutation_started = threading.Event()
        self.release_mutation = threading.Event()

    def _block_mutation(self) -> None:
        self.mutation_started.set()
        if not self.release_mutation.wait(timeout=5):
            raise TimeoutError("test did not release the Milvus mutation")

    @override
    def upsert(
        self,
        collection_name: str,
        records: Sequence[MilvusStoredRecord],
    ) -> None:
        self._block_mutation()
        super().upsert(collection_name, records)

    @override
    def delete_records(
        self,
        collection_name: str,
        records: Sequence[tuple[str, str]],
    ) -> None:
        self._block_mutation()
        super().delete_records(collection_name, records)

    @override
    def delete_entity(self, collection_name: str, entity_id: int) -> None:
        self._block_mutation()
        super().delete_entity(collection_name, entity_id)

    @override
    def delete_ids(self, collection_name: str, record_ids: Sequence[str]) -> None:
        self._block_mutation()
        super().delete_ids(collection_name, record_ids)


class StubEmbeddingProvider:
    """Minimal provider used to exercise the first-party factory path."""

    model_name = "stub"
    dimensions = 3

    async def embed_query(self, text: str) -> list[float]:
        assert text
        return [1.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _text in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


@pytest.fixture
def scope() -> VectorIndexScope:
    return VectorIndexScope(
        namespace="basic-memory-database",
        embedding_identity="Provider:model-a",
        dimensions=3,
    )


@pytest.fixture
def settings() -> MilvusSettings:
    return MilvusSettings(uri="http://localhost:19530")


def _index(
    scope: VectorIndexScope,
    settings: MilvusSettings,
    repository: FakeRepository,
) -> MilvusVectorIndex:
    return MilvusVectorIndex(
        scope,
        settings,
        repository_factory=lambda _settings: repository,
    )


def test_collection_name_uses_only_stable_project_identity(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    changed_schema = VectorIndexScope(
        namespace=scope.namespace,
        embedding_identity="Provider:model-b",
        dimensions=9,
    )

    assert collection_name(settings, scope, PROJECT) == collection_name(
        settings, changed_schema, PROJECT
    )
    assert collection_name(settings, scope, PROJECT) != collection_name(
        settings, scope, OTHER_PROJECT
    )
    assert collection_name(settings, scope, PROJECT).startswith("basic_memory_")


# --- Collection validation happens on a project's first use ---


@pytest.mark.asyncio
async def test_initialize_prepares_nothing_shared(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    """Collections are per project, so the database-wide hook has nothing to create."""
    repository = FakeRepository()

    await _index(scope, settings, repository).initialize()

    assert repository.created == []
    assert repository.closed == 0


@pytest.mark.asyncio
async def test_first_use_creates_missing_collection_once(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository()
    index = _index(scope, settings, repository)

    await index.search(QUERY, limit=1, projects=PROJECTS)
    await index.search(QUERY, limit=1, projects=PROJECTS)

    assert repository.created == [(collection_name(settings, scope, PROJECT), scope.dimensions)]
    # One validation plus one search per call.
    assert repository.closed == 3


@pytest.mark.asyncio
async def test_first_use_accepts_compatible_collection_create_race(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(create_result=False, race_dimensions=scope.dimensions)

    await _index(scope, settings, repository).search(QUERY, limit=1, projects=PROJECTS)

    assert repository.created == [(collection_name(settings, scope, PROJECT), scope.dimensions)]
    assert repository.dimensions == scope.dimensions
    assert repository.loaded == [collection_name(settings, scope, PROJECT)]


@pytest.mark.asyncio
async def test_first_use_rejects_incompatible_collection_create_race(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(create_result=False, race_dimensions=99)

    with pytest.raises(RuntimeError, match="Refusing to replace shared vector storage"):
        await _index(scope, settings, repository).search(QUERY, limit=1, projects=PROJECTS)

    assert repository.dimensions == 99
    assert repository.loaded == []


@pytest.mark.asyncio
async def test_first_use_rejects_collection_disappearing_after_create_race(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(create_result=False)

    with pytest.raises(RuntimeError, match="disappeared after a concurrent create"):
        await _index(scope, settings, repository).search(QUERY, limit=1, projects=PROJECTS)


@pytest.mark.asyncio
async def test_first_use_preserves_collection_on_dimension_mismatch(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=99)
    index = _index(scope, settings, repository)

    with pytest.raises(RuntimeError, match="Refusing to replace shared vector storage"):
        await index.search(QUERY, limit=1, projects=PROJECTS)

    assert repository.created == []
    assert repository.dimensions == 99
    assert repository.loaded == []


@pytest.mark.asyncio
async def test_first_use_accepts_matching_collection(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)

    await _index(scope, settings, repository).search(QUERY, limit=1, projects=PROJECTS)

    assert repository.created == []
    assert repository.loaded == [collection_name(settings, scope, PROJECT)]


@pytest.mark.asyncio
async def test_concurrent_first_use_rechecks_state_inside_lock(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository()
    index = _index(scope, settings, repository)
    await index._collection_lock.acquire()
    waiting = asyncio.create_task(index._ensure_collection(PROJECT))
    await asyncio.sleep(0)

    index._ready_projects.add(PROJECT)
    index._collection_lock.release()
    assert await waiting == collection_name(settings, scope, PROJECT)

    assert repository.closed == 0


# --- Writes ---


@pytest.mark.asyncio
async def test_upsert_preserves_stable_key_generation_and_values(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    index = _index(scope, settings, repository)
    record = VectorRecord(
        key=VectorKey(entity_id=7, chunk_key="summary:0"),
        source_hash="source-a",
        values=(1.0, 0.0, -1.0),
    )

    await index.upsert(PROJECT, [record])

    collection, stored_records = repository.upserts[0]
    assert collection == collection_name(settings, scope, PROJECT)
    assert len(stored_records) == 1
    assert stored_records[0].entity_id == 7
    assert stored_records[0].chunk_key == "summary:0"
    assert stored_records[0].source_hash == "source-a"
    assert stored_records[0].values == record.values
    assert len(stored_records[0].record_id) == 64


@pytest.mark.asyncio
async def test_upsert_rejects_wrong_dimensions_before_milvus_call(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    index = _index(scope, settings, repository)

    with pytest.raises(ValueError, match="expected 3, got 2"):
        await index.upsert(
            PROJECT,
            [
                VectorRecord(
                    key=VectorKey(entity_id=7, chunk_key="summary:0"),
                    source_hash="source-a",
                    values=(1.0, 0.0),
                )
            ],
        )

    assert repository.upserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["upsert", "delete", "delete_entity", "delete_orphans"])
async def test_mutations_finish_before_propagating_cancellation(
    operation: str,
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = BlockingMutationRepository(scope.dimensions)
    repository.ids = ["orphan"]
    index = _index(scope, settings, repository)
    key = VectorKey(entity_id=7, chunk_key="summary:0")

    if operation == "upsert":
        mutation = index.upsert(
            PROJECT, [VectorRecord(key=key, source_hash="source-a", values=(1.0, 0.0, 0.0))]
        )
    elif operation == "delete":
        mutation = index.delete(PROJECT, [VectorDeletion(key=key, source_hash="source-a")])
    elif operation == "delete_entity":
        mutation = index.delete_entity(PROJECT, key.entity_id)
    else:
        mutation = index.delete_orphans(PROJECT, [])

    mutation_task = asyncio.create_task(mutation)
    async with asyncio.timeout(2):
        while not repository.mutation_started.is_set():
            await asyncio.sleep(0)

    mutation_task.cancel()
    await asyncio.sleep(0)
    assert not mutation_task.done()

    mutation_task.cancel()
    await asyncio.sleep(0)
    assert not mutation_task.done()

    repository.release_mutation.set()
    with pytest.raises(asyncio.CancelledError):
        await mutation_task
    assert repository.closed == 2


@pytest.mark.asyncio
async def test_delete_forwards_source_generation(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    index = _index(scope, settings, repository)
    key = VectorKey(entity_id=7, chunk_key="summary:0")

    await index.delete(PROJECT, [VectorDeletion(key=key, source_hash="source-a")])

    collection, deletions = repository.record_deletes[0]
    assert collection == collection_name(settings, scope, PROJECT)
    assert deletions[0][1] == "source-a"
    assert len(deletions[0][0]) == 64


@pytest.mark.asyncio
async def test_delete_entity_uses_the_named_project_collection(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    index = _index(scope, settings, repository)

    await index.delete_entity(PROJECT, 77)
    await index.delete_entity(OTHER_PROJECT, 78)

    assert repository.entity_deletes == [
        (collection_name(settings, scope, PROJECT), 77),
        (collection_name(settings, scope, OTHER_PROJECT), 78),
    ]


@pytest.mark.asyncio
async def test_reconciliation_deletes_only_absent_stable_keys(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    index = _index(scope, settings, repository)
    live_key = VectorKey(entity_id=1, chunk_key="live")
    stale_key = VectorKey(entity_id=2, chunk_key="stale")

    await index.upsert(
        PROJECT,
        [
            VectorRecord(key=live_key, source_hash="a", values=(1.0, 0.0, 0.0)),
            VectorRecord(key=stale_key, source_hash="b", values=(0.0, 1.0, 0.0)),
        ],
    )
    _, stored_records = repository.upserts[0]
    repository.ids = [record.record_id for record in stored_records]

    await index.delete_orphans(PROJECT, [live_key])

    assert repository.id_deletes == [
        (collection_name(settings, scope, PROJECT), [stored_records[1].record_id])
    ]


@pytest.mark.asyncio
async def test_reconciliation_is_noop_without_orphans(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    index = _index(scope, settings, repository)

    await index.delete_orphans(PROJECT, [])

    assert repository.id_deletes == []


@pytest.mark.asyncio
async def test_reconciliation_deletes_orphans_incrementally(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    repository.ids = [f"orphan-{index}" for index in range(600)]
    index = _index(scope, settings, repository)

    await index.delete_orphans(PROJECT, [])

    assert [len(record_ids) for _, record_ids in repository.id_deletes] == [256, 256, 88]


# --- Search ---


@pytest.mark.asyncio
async def test_search_clamps_milvus_cosine_scores_and_orders_ties(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    repository.matches = [
        MilvusStoredMatch(entity_id=3, chunk_key="c", score=-1.0),
        MilvusStoredMatch(entity_id=2, chunk_key="b", score=0.1),
        MilvusStoredMatch(entity_id=1, chunk_key="a", score=1.0),
        MilvusStoredMatch(entity_id=0, chunk_key="z", score=2.0),
    ]
    index = _index(scope, settings, repository)

    matches = await index.search(QUERY, limit=4, projects=PROJECTS)

    assert [match.similarity for match in matches] == [1.0, 1.0, 0.1, 0.0]
    assert [match.key.entity_id for match in matches] == [0, 1, 2, 3]
    assert repository.searches == [(collection_name(settings, scope, PROJECT), QUERY, 4)]


@pytest.mark.asyncio
async def test_search_merges_the_collections_in_scope(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    """Milvus has no cross-collection search, so a wider scope merges per-project answers."""
    repository = FakeRepository(dimensions=scope.dimensions)
    first = collection_name(settings, scope, PROJECT)
    second = collection_name(settings, scope, OTHER_PROJECT)
    repository.matches_by_collection = {
        first: [
            MilvusStoredMatch(entity_id=1, chunk_key="a", score=0.9),
            MilvusStoredMatch(entity_id=2, chunk_key="a", score=0.3),
        ],
        second: [
            MilvusStoredMatch(entity_id=3, chunk_key="a", score=0.8),
            MilvusStoredMatch(entity_id=4, chunk_key="a", score=0.7),
        ],
    }
    index = _index(scope, settings, repository)

    matches = await index.search(QUERY, limit=3, projects=ProjectScope.of([OTHER_PROJECT, PROJECT]))

    assert [(match.key.entity_id, match.similarity) for match in matches] == [
        (1, 0.9),
        (3, 0.8),
        (4, 0.7),
    ]
    # Each project's collection is asked for its own top ``limit`` before the merge.
    assert repository.searches == [(first, QUERY, 3), (second, QUERY, 3)]
    # Both collections were validated before being searched.
    assert repository.loaded == [first, second]


@pytest.mark.asyncio
async def test_empty_operations_do_not_touch_milvus(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository()
    index = _index(scope, settings, repository)

    await index.upsert(PROJECT, [])
    await index.delete(PROJECT, [])
    assert await index.search([], limit=10, projects=PROJECTS) == []
    assert await index.search(QUERY, limit=0, projects=PROJECTS) == []
    assert await index.search(QUERY, limit=10, projects=ProjectScope.of([])) == []

    assert repository.closed == 0


def test_first_party_factory_loads_milvus() -> None:
    app_config = BasicMemoryConfig(
        env="test",
        semantic_vector_index="milvus",
        milvus_uri="https://zilliz.example",
    )
    session_maker = async_sessionmaker[AsyncSession]()

    name, index = create_semantic_vector_index(
        session_maker=session_maker,
        app_config=app_config,
        database_backend=DatabaseBackend.POSTGRES,
        embedding_provider=StubEmbeddingProvider(),
    )

    assert name == "milvus"
    assert isinstance(index, MilvusVectorIndex)
    assert isinstance(index, SemanticVectorIndex)
    assert isinstance(index, SemanticVectorIndexReconciler)
    assert index.scope.dimensions == 3
