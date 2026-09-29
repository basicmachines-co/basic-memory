"""Unit tests for the built-in pgvector semantic index adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from basic_memory.repository import pgvector_index as pgvector_index_module
from basic_memory.repository.pgvector_index import (
    PgVectorIndex,
    hnsw_ef_search_for,
    pgvector_supports_iterative_scan,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import (
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorRecord,
)


class FakeResult:
    """Small SQLAlchemy result stand-in for adapter boundary tests."""

    def __init__(
        self,
        *,
        rows: list[dict[str, object]] | None = None,
        scalar: object | None = None,
        fetchone: object | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self._fetchone = fetchone

    def fetchone(self) -> object | None:
        return self._fetchone

    def scalar_one_or_none(self) -> object | None:
        return self._scalar

    def scalar_one(self) -> object:
        assert self._scalar is not None
        return self._scalar

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class FakeSession:
    """Query-aware session that records adapter SQL without PostgreSQL."""

    def __init__(
        self,
        *,
        table_exists: bool = False,
        dimensions: int | None = None,
        has_source_hash: bool = True,
        chunk_rows: list[dict[str, object]] | None = None,
        search_rows: list[dict[str, object]] | None = None,
        fail_extension: bool = False,
        pgvector_version: str = "0.8.0",
    ) -> None:
        self.table_exists = table_exists
        self.dimensions = dimensions
        self.has_source_hash = has_source_hash
        self.chunk_rows = chunk_rows or []
        self.search_rows = search_rows or []
        self.fail_extension = fail_extension
        self.pgvector_version = pgvector_version
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self.commit_count = 0

    async def execute(
        self,
        statement: object,
        params: dict[str, object] | None = None,
    ) -> FakeResult:
        sql = str(statement)
        self.calls.append((sql, params))
        if "CREATE EXTENSION" in sql and self.fail_extension:
            raise RuntimeError("extension unavailable")
        if "SELECT extversion" in sql:
            return FakeResult(scalar=self.pgvector_version)
        if "information_schema.tables" in sql:
            return FakeResult(fetchone=(1,) if self.table_exists else None)
        if "attname = 'source_hash'" in sql:
            return FakeResult(scalar=1 if self.has_source_hash else None)
        if "SELECT atttypmod" in sql:
            return FakeResult(scalar=self.dimensions)
        if "SELECT id, entity_id, chunk_key" in sql:
            return FakeResult(rows=self.chunk_rows)
        if "AS similarity" in sql:
            return FakeResult(rows=self.search_rows)
        return FakeResult()

    async def commit(self) -> None:
        self.commit_count += 1


PROJECT = 7
PROJECTS = ProjectScope.single(PROJECT)


def _scope(dimensions: int = 4) -> VectorIndexScope:
    return VectorIndexScope(
        namespace="basic-memory-test",
        embedding_identity="stub:4",
        dimensions=dimensions,
    )


def _install_session(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> None:
    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object) -> AsyncIterator[FakeSession]:
        yield session

    monkeypatch.setattr(pgvector_index_module.db, "scoped_session", fake_scoped_session)


def _sql_calls(session: FakeSession) -> list[str]:
    return [sql for sql, _params in session.calls]


@pytest.mark.asyncio
async def test_initialize_creates_storage_once_and_invalidates_manifest(monkeypatch) -> None:
    session = FakeSession()
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())

    await index.initialize()
    await index.initialize()

    sql_calls = _sql_calls(session)
    assert sum("CREATE EXTENSION" in sql for sql in sql_calls) == 1
    assert any("embedding vector(4)" in sql for sql in sql_calls)
    assert any("USING hnsw" in sql for sql in sql_calls)
    assert any("embedding_status = 'pending'" in sql for sql in sql_calls)
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_initialize_preserves_manifest_when_storage_is_unchanged(monkeypatch) -> None:
    session = FakeSession(table_exists=True, dimensions=4)
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())

    await index.initialize()

    sql_calls = _sql_calls(session)
    assert not any("DROP TABLE IF EXISTS search_vector_embeddings" in sql for sql in sql_calls)
    assert not any("embedding_status = 'pending'" in sql for sql in sql_calls)
    table_probe = next(sql for sql in sql_calls if "information_schema.tables" in sql)
    assert "table_schema = ANY (current_schemas(false))" in table_probe


@pytest.mark.asyncio
async def test_initialize_rebuilds_dimension_mismatch_and_invalidates_manifest(monkeypatch) -> None:
    session = FakeSession(table_exists=True, dimensions=8)
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())

    await index.initialize()

    sql_calls = _sql_calls(session)
    assert any("DROP TABLE IF EXISTS search_vector_embeddings" in sql for sql in sql_calls)
    assert any("embedding_status = 'pending'" in sql for sql in sql_calls)


@pytest.mark.asyncio
async def test_initialize_rebuilds_storage_without_source_generation(monkeypatch) -> None:
    session = FakeSession(table_exists=True, dimensions=4, has_source_hash=False)
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())

    await index.initialize()

    sql_calls = _sql_calls(session)
    assert any("DROP TABLE IF EXISTS search_vector_embeddings" in sql for sql in sql_calls)
    assert any("source_hash TEXT NOT NULL" in sql for sql in sql_calls)
    assert any("embedding_status = 'pending'" in sql for sql in sql_calls)


@pytest.mark.asyncio
async def test_initialize_reports_missing_pgvector_extension(monkeypatch) -> None:
    session = FakeSession(fail_extension=True)
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())

    with pytest.raises(SemanticDependenciesMissingError, match="pgvector extension"):
        await index.initialize()


@pytest.mark.asyncio
async def test_upsert_resolves_stable_keys_and_writes_one_batch(monkeypatch) -> None:
    key_a = VectorKey(entity_id=11, chunk_key="entity:11:0")
    key_b = VectorKey(entity_id=11, chunk_key="entity:11:1")
    session = FakeSession(
        chunk_rows=[
            {
                "id": 101,
                "entity_id": 11,
                "chunk_key": key_a.chunk_key,
                "source_hash": "hash-a",
            },
            {
                "id": 102,
                "entity_id": 11,
                "chunk_key": key_b.chunk_key,
                "source_hash": "hash-b",
            },
        ]
    )
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    await index.upsert(
        PROJECT,
        [
            VectorRecord(key=key_a, source_hash="hash-a", values=(1.0, 0.0, 0.0, 0.0)),
            VectorRecord(key=key_b, source_hash="hash-b", values=(0.0, 1.0, 0.0, 0.0)),
        ],
    )

    insert_call = next(call for call in session.calls if "INSERT INTO" in call[0])
    assert insert_call[1] == {
        "project_id": 7,
        "chunk_id_0": 101,
        "embedding_0": "[1,0,0,0]",
        "dimensions_0": 4,
        "source_hash_0": "hash-a",
        "chunk_id_1": 102,
        "embedding_1": "[0,1,0,0]",
        "dimensions_1": 4,
        "source_hash_1": "hash-b",
    }
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_upsert_skips_stale_source_generation(monkeypatch) -> None:
    key = VectorKey(entity_id=11, chunk_key="entity:11:0")
    session = FakeSession(
        chunk_rows=[
            {
                "id": 101,
                "entity_id": 11,
                "chunk_key": key.chunk_key,
                "source_hash": "new-hash",
            }
        ]
    )
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    await index.upsert(
        PROJECT, [VectorRecord(key=key, source_hash="old-hash", values=(1.0, 0.0, 0.0, 0.0))]
    )

    lock_call = next(call for call in session.calls if "SELECT id, entity_id" in call[0])
    assert "FOR UPDATE" in lock_call[0]
    assert not any("INSERT INTO" in sql for sql, _params in session.calls)
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_upsert_rejects_missing_manifest_key(monkeypatch) -> None:
    key = VectorKey(entity_id=12, chunk_key="entity:12:0")
    session = FakeSession()
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    with pytest.raises(RuntimeError, match="manifest rows are missing"):
        await index.upsert(
            PROJECT, [VectorRecord(key=key, source_hash="hash", values=(1.0, 0.0, 0.0, 0.0))]
        )


@pytest.mark.asyncio
async def test_delete_stable_keys_and_entity(monkeypatch) -> None:
    key = VectorKey(entity_id=13, chunk_key="entity:13:0")
    session = FakeSession(chunk_rows=[{"id": 103, "entity_id": 13, "chunk_key": key.chunk_key}])
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    await index.delete(PROJECT, [])
    await index.delete(PROJECT, [VectorDeletion(key=key, source_hash="hash")])
    await index.delete_entity(PROJECT, 13)
    await index.delete_orphans(PROJECT, [key])

    delete_lock = next(call for call in session.calls if "SELECT id, entity_id" in call[0])
    assert "source_hash = :source_hash_0" in delete_lock[0]
    assert "embedding_status = 'pending'" in delete_lock[0]
    assert "FOR UPDATE" in delete_lock[0]
    delete_calls = [call for call in session.calls if "DELETE FROM" in call[0]]
    assert len(delete_calls) == 4
    assert delete_calls[0][1] == {"chunk_id_0": 103}
    assert delete_calls[1][1] == {"chunk_id_0": 103}
    assert delete_calls[2][1] == {"project_id": 7, "entity_id": 13}
    assert delete_calls[3][1] == {
        "project_id": 7,
        "embedding_identity": "stub:4",
    }
    assert session.commit_count == 3


@pytest.mark.asyncio
async def test_search_returns_normalized_stable_matches(monkeypatch) -> None:
    session = FakeSession(
        search_rows=[
            {"entity_id": 14, "chunk_key": "entity:14:0", "similarity": 1.4},
            {"entity_id": 15, "chunk_key": "entity:15:0", "similarity": -0.2},
        ]
    )
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    assert await index.search([], limit=5, projects=PROJECTS) == []
    assert await index.search([1.0, 0.0, 0.0, 0.0], limit=0, projects=PROJECTS) == []
    assert await index.search([1.0, 0.0, 0.0, 0.0], limit=5, projects=ProjectScope.of([])) == []
    with pytest.raises(ValueError, match="expected 4, got 2"):
        await index.search([1.0, 0.0], limit=5, projects=PROJECTS)
    assert session.calls == []

    matches = await index.search([1.0, 0.0, 0.0, 0.0], limit=5, projects=PROJECTS)

    assert [(match.key.entity_id, match.similarity) for match in matches] == [
        (14, 1.0),
        (15, 0.0),
    ]
    search_call = next(call for call in session.calls if "AS similarity" in call[0])
    assert "ORDER BY distance ASC, entity_id ASC, chunk_key ASC" in search_call[0]
    assert search_call[1] == {
        "query": "[1,0,0,0]",
        "scope_0": 7,
        "dimensions": 4,
        "embedding_identity": "stub:4",
        "limit": 5,
    }


@pytest.mark.asyncio
async def test_search_binds_every_project_in_scope(monkeypatch) -> None:
    """A multi-project scope filters both the embedding and manifest rows by the same ids."""
    session = FakeSession(search_rows=[])
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    await index.search([1.0, 0.0, 0.0, 0.0], limit=5, projects=ProjectScope.of([9, 7]))

    sql, params = next(call for call in session.calls if "AS similarity" in call[0])
    assert params is not None
    assert "WHERE e.project_id IN (:scope_0, :scope_1)" in sql
    assert "AND c.project_id IN (:scope_0, :scope_1)" in sql
    assert params["scope_0"] == 7
    assert params["scope_1"] == 9


def _settings(session: FakeSession) -> list[tuple[str, dict[str, object] | None]]:
    return [call for call in session.calls if "set_config" in call[0]]


@pytest.mark.parametrize(
    ("extversion", "expected"),
    [("0.7.4", False), ("0.8.0", True), ("0.8.1", True), ("1.0.0", True), ("garbage", False)],
)
def test_iterative_scan_arrived_in_pgvector_0_8(extversion: str, expected: bool) -> None:
    assert pgvector_supports_iterative_scan(extversion) is expected


def test_ef_search_is_sized_to_the_window_within_the_server_bounds() -> None:
    assert hnsw_ef_search_for(5) == 40
    assert hnsw_ef_search_for(40) == 40
    assert hnsw_ef_search_for(250) == 250
    assert hnsw_ef_search_for(5000) == 1000


@pytest.mark.asyncio
async def test_initialize_requires_a_pgvector_that_can_keep_scanning(monkeypatch) -> None:
    """An extension too old to fill a filtered window is a deployment error, not a quiet gap."""
    older = FakeSession(pgvector_version="0.7.4")
    _install_session(monkeypatch, older)
    index = PgVectorIndex(MagicMock(), _scope())

    with pytest.raises(SemanticDependenciesMissingError, match="pgvector 0.7.4 predates"):
        await index.initialize()

    assert not any("CREATE TABLE" in sql for sql in _sql_calls(older))
    assert index._initialized is False


@pytest.mark.asyncio
async def test_search_sizes_the_scan_to_the_window_it_must_fill(monkeypatch) -> None:
    """A 250-row candidate window asks HNSW for 250 candidates, not the default 40."""
    session = FakeSession(search_rows=[])
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    await index.search([1.0, 0.0, 0.0, 0.0], limit=250, projects=PROJECTS)

    ef_search = next(call for call in _settings(session) if "hnsw.ef_search" in call[0])
    assert "set_config('hnsw.ef_search', :ef_search, true)" in ef_search[0]
    assert ef_search[1] == {"ef_search": "250"}
    search_sql = next(sql for sql, _params in session.calls if "AS similarity" in sql)
    # The window is taken by distance inside the CTE, then re-sorted with tie-breaks.
    assert "ORDER BY e.embedding <=> CAST(:query AS vector) LIMIT :limit" in search_sql


@pytest.mark.asyncio
async def test_search_keeps_scanning_until_the_window_fills(monkeypatch) -> None:
    session = FakeSession(search_rows=[])
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    await index.search([1.0, 0.0, 0.0, 0.0], limit=5, projects=PROJECTS)

    settings = [sql for sql, _params in _settings(session)]
    assert any("'hnsw.ef_search'" in sql for sql in settings)
    assert any("'hnsw.iterative_scan', 'relaxed_order', true" in sql for sql in settings)
    # Settings precede the scan they configure, inside the same session.
    ordered = [sql for sql, _params in session.calls]
    assert max(ordered.index(sql) for sql in settings) < next(
        position for position, sql in enumerate(ordered) if "AS similarity" in sql
    )
