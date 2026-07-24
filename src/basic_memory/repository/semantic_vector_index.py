"""Storage contract for semantic vector index backends."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


SEMANTIC_VECTOR_INDEX_ENTRY_POINT_GROUP = "basic_memory.semantic_vector_indexes"


@dataclass(frozen=True, slots=True)
class VectorIndexScope:
    """Stable isolation and embedding identity for one project's vectors."""

    namespace: str
    project_id: int
    embedding_identity: str
    dimensions: int


@dataclass(frozen=True, slots=True)
class VectorKey:
    """Backend-independent identity for one semantic chunk vector."""

    entity_id: int
    chunk_key: str


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """One vector value to insert or replace idempotently."""

    key: VectorKey
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class VectorMatch:
    """One nearest-neighbour match with normalized cosine similarity."""

    key: VectorKey
    similarity: float


@runtime_checkable
class SemanticVectorIndex(Protocol):
    """Narrow storage boundary implemented by built-in and external indexes.

    The contract deliberately has no SQLAlchemy session and never calls an
    embedding provider. Core owns the SQL manifest and embedding lifecycle;
    implementations own only vector persistence and nearest-neighbour lookup.
    """

    scope: VectorIndexScope

    async def initialize(self) -> None:
        """Create or validate backend storage for the configured scope."""
        ...

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        """Insert or replace vectors by stable key."""
        ...

    async def delete(self, keys: Sequence[VectorKey]) -> None:
        """Delete vectors by stable key; missing keys are successful no-ops."""
        ...

    async def delete_entity(self, entity_id: int) -> None:
        """Delete every vector owned by an entity in this scope."""
        ...

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
    ) -> list[VectorMatch]:
        """Return nearest matches ordered by normalized cosine similarity."""
        ...


@runtime_checkable
class SemanticVectorIndexReconciler(Protocol):
    """Optional cleanup capability for removing vectors absent from the live manifest."""

    scope: VectorIndexScope

    async def delete_orphans(self, live_keys: Sequence[VectorKey]) -> None:
        """Delete scoped vectors whose stable keys are not in ``live_keys``."""
        ...


def validate_vector_dimensions(
    scope: VectorIndexScope,
    records: Sequence[VectorRecord],
) -> None:
    """Fail before a backend write when a vector has the wrong dimensions."""
    for record in records:
        if len(record.values) != scope.dimensions:
            raise ValueError(
                "Vector dimensions do not match the configured index scope: "
                f"expected {scope.dimensions}, got {len(record.values)} "
                f"for {record.key.chunk_key}."
            )


def validate_query_dimensions(scope: VectorIndexScope, query: Sequence[float]) -> None:
    """Fail before a backend query when the query vector has the wrong dimensions."""
    if len(query) != scope.dimensions:
        raise ValueError(
            "Query dimensions do not match the configured index scope: "
            f"expected {scope.dimensions}, got {len(query)}."
        )
