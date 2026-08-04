"""Milvus implementation of Basic Memory's semantic vector index contract."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Sequence

from basic_memory.repository.milvus_config import MilvusSettings
from basic_memory.repository.milvus_repository import (
    MilvusRepository,
    MilvusStoredMatch,
    MilvusStoredRecord,
    create_repository,
)
from basic_memory.repository.semantic_vector_index import (
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
    validate_query_dimensions,
    validate_vector_dimensions,
)

type MilvusRepositoryFactory = Callable[[MilvusSettings], MilvusRepository]

_ORPHAN_DELETE_BATCH_SIZE = 256


def _record_id(key: VectorKey) -> str:
    stable_key = f"{key.entity_id}\0{key.chunk_key}".encode()
    return hashlib.sha256(stable_key).hexdigest()


def collection_name(settings: MilvusSettings, scope: VectorIndexScope) -> str:
    """Return a stable project collection name independent of embedding schema."""
    namespace_digest = hashlib.sha256(scope.namespace.encode()).hexdigest()[:24]
    return f"{settings.collection_prefix}_{namespace_digest}_{scope.project_id}"


def _normalize_cosine_score(score: float) -> float:
    """Clamp Milvus COSINE similarity to Basic Memory's shared score range."""
    return max(0.0, min(1.0, score))


class MilvusVectorIndex:
    """Persist and query one Basic Memory project's vectors in Milvus."""

    def __init__(
        self,
        scope: VectorIndexScope,
        settings: MilvusSettings,
        *,
        repository_factory: MilvusRepositoryFactory = create_repository,
    ) -> None:
        self.scope = scope
        self._settings = settings
        self._collection_name = collection_name(settings, scope)
        self._repository_factory = repository_factory
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    def _with_repository[T](self, operation: Callable[[MilvusRepository], T]) -> T:
        repository = self._repository_factory(self._settings)
        try:
            return operation(repository)
        finally:
            repository.close()

    def _initialize_blocking(self) -> None:
        def initialize_repository(repository: MilvusRepository) -> None:
            dimensions = repository.collection_dimensions(self._collection_name)
            if dimensions is None:
                created = repository.create_collection(
                    self._collection_name,
                    self.scope.dimensions,
                )
                if created:
                    return
                dimensions = repository.collection_dimensions(self._collection_name)
                if dimensions is None:
                    raise RuntimeError(
                        f"Milvus collection '{self._collection_name}' disappeared after "
                        "a concurrent create operation."
                    )
            if dimensions == self.scope.dimensions:
                # Milvus Lite releases persisted collections when the owning process exits.
                # Load only after the scope check so migrations do not load incompatible
                # remote collections before Basic Memory refuses to use them.
                repository.load_collection(self._collection_name)
                return

            # Trigger: an existing project collection uses another embedding dimension.
            # Why: automatically replacing shared storage lets mixed-version processes
            # repeatedly erase each other's vectors during a rolling deployment.
            # Outcome: preserve the collection until an operator coordinates migration.
            raise RuntimeError(
                f"Milvus collection '{self._collection_name}' has {dimensions} dimensions, "
                f"but Basic Memory is configured for {self.scope.dimensions}. Refusing to "
                "replace shared vector storage automatically; stop all writers and coordinate "
                "the collection migration before reindexing."
            )

        self._with_repository(initialize_repository)

    def _search_blocking(
        self,
        query: Sequence[float],
        limit: int,
    ) -> list[MilvusStoredMatch]:
        repository = self._repository_factory(self._settings)
        try:
            return repository.search(self._collection_name, query, limit)
        finally:
            repository.close()

    async def _run_blocking_mutation(self, operation: Callable[[], None]) -> None:
        """Keep the project mutation boundary held until the worker has stopped."""
        mutation = asyncio.create_task(asyncio.to_thread(operation))
        completed = asyncio.Event()
        mutation.add_done_callback(lambda _mutation: completed.set())
        try:
            await asyncio.shield(mutation)
        except asyncio.CancelledError:
            # asyncio cannot stop a running thread. Delay cancellation until the
            # mutation finishes so SQL state and the per-project lock cannot advance
            # while an older Milvus write is still able to land.
            while not completed.is_set():
                try:
                    await asyncio.shield(completed.wait())
                except asyncio.CancelledError:
                    continue
            mutation.exception()
            raise

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await self._run_blocking_mutation(self._initialize_blocking)
            self._initialized = True

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        validate_vector_dimensions(self.scope, records)
        await self.initialize()
        stored_records = [
            MilvusStoredRecord(
                record_id=_record_id(record.key),
                entity_id=record.key.entity_id,
                chunk_key=record.key.chunk_key,
                source_hash=record.source_hash,
                values=record.values,
            )
            for record in records
        ]
        await self._run_blocking_mutation(
            lambda: self._with_repository(
                lambda repository: repository.upsert(self._collection_name, stored_records)
            )
        )

    async def delete(self, records: Sequence[VectorDeletion]) -> None:
        if not records:
            return
        await self.initialize()
        stored_deletions = [(_record_id(record.key), record.source_hash) for record in records]
        await self._run_blocking_mutation(
            lambda: self._with_repository(
                lambda repository: repository.delete_records(
                    self._collection_name,
                    stored_deletions,
                )
            )
        )

    async def delete_entity(self, entity_id: int) -> None:
        await self.initialize()
        await self._run_blocking_mutation(
            lambda: self._with_repository(
                lambda repository: repository.delete_entity(self._collection_name, entity_id)
            )
        )

    async def delete_orphans(self, live_keys: Sequence[VectorKey]) -> None:
        await self.initialize()
        live_ids = {_record_id(key) for key in live_keys}

        def delete_missing(repository: MilvusRepository) -> None:
            orphan_ids: list[str] = []
            for record_id in repository.iter_ids(self._collection_name):
                if record_id in live_ids:
                    continue
                orphan_ids.append(record_id)
                if len(orphan_ids) == _ORPHAN_DELETE_BATCH_SIZE:
                    repository.delete_ids(self._collection_name, orphan_ids)
                    orphan_ids.clear()
            if orphan_ids:
                repository.delete_ids(self._collection_name, orphan_ids)

        await self._run_blocking_mutation(lambda: self._with_repository(delete_missing))

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
    ) -> list[VectorMatch]:
        if not query or limit <= 0:
            return []
        validate_query_dimensions(self.scope, query)
        await self.initialize()

        stored_matches = await asyncio.to_thread(self._search_blocking, query, limit)
        matches = [
            VectorMatch(
                key=VectorKey(
                    entity_id=match.entity_id,
                    chunk_key=match.chunk_key,
                ),
                similarity=_normalize_cosine_score(match.score),
            )
            for match in stored_matches
        ]
        return sorted(
            matches,
            key=lambda match: (
                -match.similarity,
                match.key.entity_id,
                match.key.chunk_key,
            ),
        )
