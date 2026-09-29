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
from basic_memory.repository.search_scope import ProjectScope
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


def collection_name(settings: MilvusSettings, scope: VectorIndexScope, project_id: int) -> str:
    """Return one project's stable collection name, independent of embedding schema."""
    namespace_digest = hashlib.sha256(scope.namespace.encode()).hexdigest()[:24]
    return f"{settings.collection_prefix}_{namespace_digest}_{project_id}"


def _normalize_cosine_score(score: float) -> float:
    """Clamp Milvus COSINE similarity to Basic Memory's shared score range."""
    return max(0.0, min(1.0, score))


class MilvusVectorIndex:
    """Persist and query Basic Memory vectors in Milvus, one collection per project."""

    def __init__(
        self,
        scope: VectorIndexScope,
        settings: MilvusSettings,
        *,
        repository_factory: MilvusRepositoryFactory = create_repository,
    ) -> None:
        self.scope = scope
        self._settings = settings
        self._repository_factory = repository_factory
        # Projects whose collection has been created or validated by this instance.
        self._ready_projects: set[int] = set()
        self._collection_lock = asyncio.Lock()

    def _with_repository[T](self, operation: Callable[[MilvusRepository], T]) -> T:
        repository = self._repository_factory(self._settings)
        try:
            return operation(repository)
        finally:
            repository.close()

    def _collection(self, project_id: int) -> str:
        return collection_name(self._settings, self.scope, project_id)

    def _validate_collection_blocking(self, collection: str) -> None:
        def initialize_repository(repository: MilvusRepository) -> None:
            dimensions = repository.collection_dimensions(collection)
            if dimensions is None:
                created = repository.create_collection(collection, self.scope.dimensions)
                if created:
                    return
                dimensions = repository.collection_dimensions(collection)
                if dimensions is None:
                    raise RuntimeError(
                        f"Milvus collection '{collection}' disappeared after "
                        "a concurrent create operation."
                    )
            if dimensions == self.scope.dimensions:
                # Milvus Lite releases persisted collections when the owning process exits.
                # Load only after the scope check so migrations do not load incompatible
                # remote collections before Basic Memory refuses to use them.
                repository.load_collection(collection)
                return

            # Trigger: an existing project collection uses another embedding dimension.
            # Why: automatically replacing shared storage lets mixed-version processes
            # repeatedly erase each other's vectors during a rolling deployment.
            # Outcome: preserve the collection until an operator coordinates migration.
            raise RuntimeError(
                f"Milvus collection '{collection}' has {dimensions} dimensions, "
                f"but Basic Memory is configured for {self.scope.dimensions}. Refusing to "
                "replace shared vector storage automatically; stop all writers and coordinate "
                "the collection migration before reindexing."
            )

        self._with_repository(initialize_repository)

    def _search_blocking(
        self,
        collection: str,
        query: Sequence[float],
        limit: int,
    ) -> list[MilvusStoredMatch]:
        repository = self._repository_factory(self._settings)
        try:
            return repository.search(collection, query, limit)
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
        """Nothing is shared across projects: each collection is validated on first use."""
        return None

    async def _ensure_collection(self, project_id: int) -> str:
        """Create or validate one project's collection once per adapter instance."""
        collection = self._collection(project_id)
        if project_id in self._ready_projects:
            return collection
        async with self._collection_lock:
            if project_id in self._ready_projects:
                return collection
            await self._run_blocking_mutation(
                lambda: self._validate_collection_blocking(collection)
            )
            self._ready_projects.add(project_id)
        return collection

    async def upsert(self, project_id: int, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        validate_vector_dimensions(self.scope, records)
        collection = await self._ensure_collection(project_id)
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
                lambda repository: repository.upsert(collection, stored_records)
            )
        )

    async def delete(self, project_id: int, records: Sequence[VectorDeletion]) -> None:
        if not records:
            return
        collection = await self._ensure_collection(project_id)
        stored_deletions = [(_record_id(record.key), record.source_hash) for record in records]
        await self._run_blocking_mutation(
            lambda: self._with_repository(
                lambda repository: repository.delete_records(collection, stored_deletions)
            )
        )

    async def delete_entity(self, project_id: int, entity_id: int) -> None:
        collection = await self._ensure_collection(project_id)
        await self._run_blocking_mutation(
            lambda: self._with_repository(
                lambda repository: repository.delete_entity(collection, entity_id)
            )
        )

    async def delete_orphans(self, project_id: int, live_keys: Sequence[VectorKey]) -> None:
        collection = await self._ensure_collection(project_id)
        live_ids = {_record_id(key) for key in live_keys}

        def delete_missing(repository: MilvusRepository) -> None:
            orphan_ids: list[str] = []
            for record_id in repository.iter_ids(collection):
                if record_id in live_ids:
                    continue
                orphan_ids.append(record_id)
                if len(orphan_ids) == _ORPHAN_DELETE_BATCH_SIZE:
                    repository.delete_ids(collection, orphan_ids)
                    orphan_ids.clear()
            if orphan_ids:
                repository.delete_ids(collection, orphan_ids)

        await self._run_blocking_mutation(lambda: self._with_repository(delete_missing))

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
        projects: ProjectScope,
    ) -> list[VectorMatch]:
        if not query or limit <= 0 or projects.is_empty:
            return []
        validate_query_dimensions(self.scope, query)

        # Milvus has no cross-collection search, so a scope wider than one project
        # asks each project's collection for its own top ``limit`` and merges them.
        matches: list[VectorMatch] = []
        for project_id in projects.project_ids:
            collection = await self._ensure_collection(project_id)
            stored_matches = await asyncio.to_thread(
                self._search_blocking, collection, query, limit
            )
            matches.extend(
                VectorMatch(
                    key=VectorKey(entity_id=match.entity_id, chunk_key=match.chunk_key),
                    similarity=_normalize_cosine_score(match.score),
                )
                for match in stored_matches
            )
        matches.sort(
            key=lambda match: (
                -match.similarity,
                match.key.entity_id,
                match.key.chunk_key,
            )
        )
        return matches[:limit]
