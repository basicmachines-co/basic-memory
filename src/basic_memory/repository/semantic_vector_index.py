"""Storage contract for semantic vector index backends."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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
class VectorAdmission:
    """Which chunks a filtered vector search may return, as SQL over the manifest.

    ``predicate`` is a boolean expression over ``search_vector_chunks AS c``. Built-in
    indexes share the SQL database with the manifest and the search rows, so they
    evaluate it inside their nearest-neighbour query and return the exact top-k of the
    admitted chunks; no candidate is fetched only to be filtered out afterwards (#1746).
    Its bind names are its own (see ``prefixed_binds``), so they never collide with an
    adapter's.
    """

    predicate: str
    params: Mapping[str, object]


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
        admission: VectorAdmission | None = None,
    ) -> list[VectorMatch]:
        """Return nearest matches within ``projects``, ordered by normalized similarity.

        With ``admission``, return the nearest ``limit`` chunks among those it admits.
        Only indexes inside the SQL database can evaluate it; an external index raises.
        """
        ...


@dataclass(frozen=True, slots=True)
class VectorRowMatch:
    """One of a search row's nearest chunks, named with the row it was cut from."""

    key: VectorKey
    similarity: float
    project_id: int
    source_type: str
    source_row_id: int


@runtime_checkable
class SourceRowVectorIndex(Protocol):
    """Nearest search rows, ranked and collapsed inside the SQL database (#1746).

    Implemented by the built-in indexes, which store vectors beside the chunk manifest.
    A note is cut into dozens of chunks, so ranking chunks and collapsing them to rows
    in the application read thousands of chunk keys to fill one page, and a filtered
    search re-read a widening window until enough rows appeared. Here the database
    ranks the chunks, keeps each row's best ``chunks_per_row``, and returns at most
    ``limit`` rows' worth.
    """

    async def search_rows(
        self,
        query: Sequence[float],
        *,
        limit: int,
        chunk_scan: int,
        projects: ProjectScope,
        admission: VectorAdmission | None = None,
        chunks_per_row: int = 1,
    ) -> list[VectorRowMatch]:
        """Return the ``limit`` nearest rows' best chunks, nearest row first.

        A row's chunks follow it, nearest first. ``chunk_scan`` bounds how many of the
        nearest chunks an approximate scan reads before collapsing them to rows; an
        exact ranking reads every admitted chunk and ignores it.
        """
        ...


def collapse_to_rows_sql(candidate_ctes: str) -> str:
    """Rank candidate chunks per search row and keep the nearest rows' best chunks.

    ``candidate_ctes`` defines a CTE named ``candidates`` with the columns
    ``entity_id, chunk_key, project_id, source_type, source_row_id, distance``.
    Binds ``:row_limit`` and ``:chunks_per_row``. Ties order by row location and chunk
    key, so the ranking is the same on every page of a query. Both engines run it.
    """
    return (
        f"WITH {candidate_ctes}, "
        "ranked AS ("
        "SELECT candidates.*, ROW_NUMBER() OVER ("
        "PARTITION BY project_id, source_type, source_row_id "
        "ORDER BY distance, entity_id, chunk_key) AS chunk_rank "
        "FROM candidates), "
        "nearest_rows AS ("
        "SELECT project_id, source_type, source_row_id, distance AS row_distance "
        "FROM ranked WHERE chunk_rank = 1 "
        "ORDER BY distance, project_id, source_type, source_row_id "
        "LIMIT :row_limit) "
        "SELECT r.entity_id, r.chunk_key, r.project_id, r.source_type, r.source_row_id, "
        "r.distance FROM ranked r JOIN nearest_rows n "
        "ON n.project_id = r.project_id AND n.source_type = r.source_type "
        "AND n.source_row_id = r.source_row_id "
        "WHERE r.chunk_rank <= :chunks_per_row "
        "ORDER BY n.row_distance, n.project_id, n.source_type, n.source_row_id, r.chunk_rank"
    )


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


def prefixed_binds(
    sql: str, params: Mapping[str, object], prefix: str
) -> tuple[str, dict[str, object]]:
    """Rename every ``:name`` bind in ``sql`` to ``:{prefix}name``.

    A compiled filter names its binds for its own statement (``scope_0``, ``after_date``
    and so on); embedded in another statement those names could collide. Longer names
    are replaced first so ``:scope_1`` never rewrites the head of ``:scope_10``.
    """
    renamed = sql
    for name in sorted(params, key=len, reverse=True):
        renamed = re.sub(rf"(?<![:\w]):{re.escape(name)}\b", f":{prefix}{name}", renamed)
    return renamed, {f"{prefix}{name}": value for name, value in params.items()}
