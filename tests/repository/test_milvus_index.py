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
        return self.matches

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
        project_id=42,
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


def test_collection_name_uses_only_stable_scope_identity(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    changed_schema = VectorIndexScope(
        namespace=scope.namespace,
        project_id=scope.project_id,
        embedding_identity="Provider:model-b",
        dimensions=9,
    )
    other_project = VectorIndexScope(
        namespace=scope.namespace,
        project_id=43,
        embedding_identity=scope.embedding_identity,
        dimensions=scope.dimensions,
    )

    assert collection_name(settings, scope) == collection_name(settings, changed_schema)
    assert collection_name(settings, scope) != collection_name(settings, other_project)
    assert collection_name(settings, scope).startswith("basic_memory_")


@pytest.mark.asyncio
async def test_initialize_creates_missing_collection_once(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository()
    index = _index(scope, settings, repository)

    await index.initialize()
    await index.initialize()

    assert repository.created == [(collection_name(settings, scope), scope.dimensions)]
    assert repository.closed == 1


@pytest.mark.asyncio
async def test_initialize_accepts_compatible_collection_create_race(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(create_result=False, race_dimensions=scope.dimensions)

    await _index(scope, settings, repository).initialize()

    assert repository.created == [(collection_name(settings, scope), scope.dimensions)]
    assert repository.dimensions == scope.dimensions
    assert repository.loaded == [collection_name(settings, scope)]


@pytest.mark.asyncio
async def test_initialize_rejects_incompatible_collection_create_race(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(create_result=False, race_dimensions=99)

    with pytest.raises(RuntimeError, match="Refusing to replace shared vector storage"):
        await _index(scope, settings, repository).initialize()

    assert repository.dimensions == 99
    assert repository.loaded == []


@pytest.mark.asyncio
async def test_initialize_rejects_collection_disappearing_after_create_race(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(create_result=False)

    with pytest.raises(RuntimeError, match="disappeared after a concurrent create"):
        await _index(scope, settings, repository).initialize()


@pytest.mark.asyncio
async def test_initialize_preserves_collection_on_dimension_mismatch(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=99)
    index = _index(scope, settings, repository)

    with pytest.raises(RuntimeError, match="Refusing to replace shared vector storage"):
        await index.initialize()

    assert repository.created == []
    assert repository.dimensions == 99
    assert repository.loaded == []


@pytest.mark.asyncio
async def test_initialize_accepts_matching_collection(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)

    await _index(scope, settings, repository).initialize()

    assert repository.created == []
    assert repository.loaded == [collection_name(settings, scope)]


@pytest.mark.asyncio
async def test_concurrent_initialize_rechecks_state_inside_lock(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository()
    index = _index(scope, settings, repository)
    await index._initialize_lock.acquire()
    waiting_initialize = asyncio.create_task(index.initialize())
    await asyncio.sleep(0)

    index._initialized = True
    index._initialize_lock.release()
    await waiting_initialize

    assert repository.closed == 0


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

    await index.upsert([record])

    _, stored_records = repository.upserts[0]
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
            [
                VectorRecord(
                    key=VectorKey(entity_id=7, chunk_key="summary:0"),
                    source_hash="source-a",
                    values=(1.0, 0.0),
                )
            ]
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
            [VectorRecord(key=key, source_hash="source-a", values=(1.0, 0.0, 0.0))]
        )
    elif operation == "delete":
        mutation = index.delete([VectorDeletion(key=key, source_hash="source-a")])
    elif operation == "delete_entity":
        mutation = index.delete_entity(key.entity_id)
    else:
        mutation = index.delete_orphans([])

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

    await index.delete([VectorDeletion(key=key, source_hash="source-a")])

    _, deletions = repository.record_deletes[0]
    assert deletions[0][1] == "source-a"
    assert len(deletions[0][0]) == 64


@pytest.mark.asyncio
async def test_delete_entity_uses_project_collection(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    index = _index(scope, settings, repository)

    await index.delete_entity(77)

    assert repository.entity_deletes == [(collection_name(settings, scope), 77)]


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
        [
            VectorRecord(key=live_key, source_hash="a", values=(1.0, 0.0, 0.0)),
            VectorRecord(key=stale_key, source_hash="b", values=(0.0, 1.0, 0.0)),
        ]
    )
    _, stored_records = repository.upserts[0]
    repository.ids = [record.record_id for record in stored_records]

    await index.delete_orphans([live_key])

    assert repository.id_deletes == [
        (collection_name(settings, scope), [stored_records[1].record_id])
    ]


@pytest.mark.asyncio
async def test_reconciliation_is_noop_without_orphans(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    index = _index(scope, settings, repository)

    await index.delete_orphans([])

    assert repository.id_deletes == []


@pytest.mark.asyncio
async def test_reconciliation_deletes_orphans_incrementally(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository(dimensions=scope.dimensions)
    repository.ids = [f"orphan-{index}" for index in range(600)]
    index = _index(scope, settings, repository)

    await index.delete_orphans([])

    assert [len(record_ids) for _, record_ids in repository.id_deletes] == [256, 256, 88]


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

    matches = await index.search([1.0, 0.0, 0.0], limit=4)

    assert [match.similarity for match in matches] == [1.0, 1.0, 0.1, 0.0]
    assert [match.key.entity_id for match in matches] == [0, 1, 2, 3]
    assert repository.searches == [(collection_name(settings, scope), [1.0, 0.0, 0.0], 4)]


@pytest.mark.asyncio
async def test_empty_operations_do_not_initialize(
    scope: VectorIndexScope,
    settings: MilvusSettings,
) -> None:
    repository = FakeRepository()
    index = _index(scope, settings, repository)

    await index.upsert([])
    await index.delete([])
    assert await index.search([], limit=10) == []
    assert await index.search([1.0, 0.0, 0.0], limit=0) == []

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
        project_id=42,
        app_config=app_config,
        database_backend=DatabaseBackend.POSTGRES,
        embedding_provider=StubEmbeddingProvider(),
    )

    assert name == "milvus"
    assert isinstance(index, MilvusVectorIndex)
    assert isinstance(index, SemanticVectorIndex)
    assert isinstance(index, SemanticVectorIndexReconciler)
    assert index.scope.project_id == 42
