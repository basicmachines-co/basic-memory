"""Storage contract for semantic vector index backends."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from basic_memory.repository.search_scope import ProjectScope


@dataclass(frozen=True, slots=True)
class VectorIndexScope:
    """The database namespace and embedding schema an adapter stores vectors under.

    Projects are partitions inside it: every write names the project it touches and a
    search names the projects it reads, so one adapter serves a whole database.
    """

    namespace: str
    embedding_identity: str
    dimensions: int


@dataclass(frozen=True, slots=True)
class VectorKey:
    """Backend-independent identity for one semantic chunk vector.

    Entity ids are database-wide primary keys, so the pair is unique across projects.
    """

    entity_id: int
    chunk_key: str


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """One source-generation vector value to insert or replace idempotently."""

    key: VectorKey
    source_hash: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class VectorDeletion:
    """One source-generation vector value to remove idempotently."""

    key: VectorKey
    source_hash: str


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

    @property
    def scope(self) -> VectorIndexScope: ...

    async def initialize(self) -> None:
        """Create or validate backend storage shared by every project in the scope."""
        ...

    async def upsert(self, project_id: int, records: Sequence[VectorRecord]) -> None:
        """Insert or replace one project's vectors, only for each record's source generation."""
        ...

    async def delete(self, project_id: int, records: Sequence[VectorDeletion]) -> None:
        """Delete one project's vectors, only for each record's source generation."""
        ...

    async def delete_entity(self, project_id: int, entity_id: int) -> None:
        """Delete every vector owned by an entity in one project."""
        ...

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
        projects: ProjectScope,
    ) -> list[VectorMatch]:
        """Return nearest matches within ``projects``, ordered by normalized similarity."""
        ...


@runtime_checkable
class SemanticVectorIndexReconciler(Protocol):
    """Optional cleanup capability for removing vectors absent from the live manifest."""

    @property
    def scope(self) -> VectorIndexScope: ...

    async def delete_orphans(self, project_id: int, live_keys: Sequence[VectorKey]) -> None:
        """Delete one project's vectors whose stable keys are not in ``live_keys``."""
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
