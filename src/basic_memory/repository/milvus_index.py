"""Milvus implementation of Basic Memory's semantic vector index contract."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Sequence

from basic_memory.repository.milvus_config import MilvusSettings
from basic_memory.repository.milvus_gateway import (
    MilvusGateway,
    MilvusStoredMatch,
    MilvusStoredRecord,
    create_gateway,
)
from basic_memory.repository.semantic_vector_index import (
    SemanticVectorIndex,
    SemanticVectorIndexReconciler,
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
    validate_query_dimensions,
    validate_vector_dimensions,
)

type MilvusGatewayFactory = Callable[[MilvusSettings], MilvusGateway]

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


class MilvusVectorIndex(SemanticVectorIndex, SemanticVectorIndexReconciler):
    """Persist and query one Basic Memory project's vectors in Milvus."""

    def __init__(
        self,
        scope: VectorIndexScope,
        settings: MilvusSettings,
        *,
        gateway_factory: MilvusGatewayFactory = create_gateway,
    ) -> None:
        self.scope = scope
        self._settings = settings
        self._collection_name = collection_name(settings, scope)
        self._gateway_factory = gateway_factory
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    def _with_gateway[T](self, operation: Callable[[MilvusGateway], T]) -> T:
        gateway = self._gateway_factory(self._settings)
        try:
            return operation(gateway)
        finally:
            gateway.close()

    def _initialize_blocking(self) -> None:
        def initialize_gateway(gateway: MilvusGateway) -> None:
            dimensions = gateway.collection_dimensions(self._collection_name)
            if dimensions is None:
                created = gateway.create_collection(
                    self._collection_name,
                    self.scope.dimensions,
                )
                if created:
                    return
                dimensions = gateway.collection_dimensions(self._collection_name)
                if dimensions is None:
                    raise RuntimeError(
                        f"Milvus collection '{self._collection_name}' disappeared after "
                        "a concurrent create operation."
                    )
            if dimensions == self.scope.dimensions:
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

        self._with_gateway(initialize_gateway)

    def _search_blocking(
        self,
        query: Sequence[float],
        limit: int,
    ) -> list[MilvusStoredMatch]:
        gateway = self._gateway_factory(self._settings)
        try:
            return gateway.search(self._collection_name, query, limit)
        finally:
            gateway.close()

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
            lambda: self._with_gateway(
                lambda gateway: gateway.upsert(self._collection_name, stored_records)
            )
        )

    async def delete(self, records: Sequence[VectorDeletion]) -> None:
        if not records:
            return
        await self.initialize()
        stored_deletions = [(_record_id(record.key), record.source_hash) for record in records]
        await self._run_blocking_mutation(
            lambda: self._with_gateway(
                lambda gateway: gateway.delete_records(
                    self._collection_name,
                    stored_deletions,
                )
            )
        )

    async def delete_entity(self, entity_id: int) -> None:
        await self.initialize()
        await self._run_blocking_mutation(
            lambda: self._with_gateway(
                lambda gateway: gateway.delete_entity(self._collection_name, entity_id)
            )
        )

    async def delete_orphans(self, live_keys: Sequence[VectorKey]) -> None:
        await self.initialize()
        live_ids = {_record_id(key) for key in live_keys}

        def delete_missing(gateway: MilvusGateway) -> None:
            orphan_ids: list[str] = []
            for record_id in gateway.iter_ids(self._collection_name):
                if record_id in live_ids:
                    continue
                orphan_ids.append(record_id)
                if len(orphan_ids) == _ORPHAN_DELETE_BATCH_SIZE:
                    gateway.delete_ids(self._collection_name, orphan_ids)
                    orphan_ids.clear()
            if orphan_ids:
                gateway.delete_ids(self._collection_name, orphan_ids)

        await self._run_blocking_mutation(lambda: self._with_gateway(delete_missing))

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
