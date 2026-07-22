"""Unit tests for the built-in pgvector semantic index adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from basic_memory.repository import pgvector_index as pgvector_index_module
from basic_memory.repository.pgvector_index import PgVectorIndex
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import (
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
        chunk_rows: list[dict[str, object]] | None = None,
        search_rows: list[dict[str, object]] | None = None,
        fail_extension: bool = False,
    ) -> None:
        self.table_exists = table_exists
        self.dimensions = dimensions
        self.chunk_rows = chunk_rows or []
        self.search_rows = search_rows or []
        self.fail_extension = fail_extension
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
        if "information_schema.tables" in sql:
            return FakeResult(fetchone=(1,) if self.table_exists else None)
        if "SELECT atttypmod" in sql:
            return FakeResult(scalar=self.dimensions)
        if "SELECT id, entity_id, chunk_key" in sql:
            return FakeResult(rows=self.chunk_rows)
        if "AS similarity" in sql:
            return FakeResult(rows=self.search_rows)
        return FakeResult()

    async def commit(self) -> None:
        self.commit_count += 1


def _scope(dimensions: int = 4) -> VectorIndexScope:
    return VectorIndexScope(
        namespace="basic-memory-test",
        project_id=7,
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
async def test_initialize_creates_storage_once(monkeypatch) -> None:
    session = FakeSession()
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())

    await index.initialize()
    await index.initialize()

    sql_calls = _sql_calls(session)
    assert sum("CREATE EXTENSION" in sql for sql in sql_calls) == 1
    assert any("embedding vector(4)" in sql for sql in sql_calls)
    assert any("USING hnsw" in sql for sql in sql_calls)
    assert not any("embedding_status = 'pending'" in sql for sql in sql_calls)
    assert session.commit_count == 1


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
            {"id": 101, "entity_id": 11, "chunk_key": key_a.chunk_key},
            {"id": 102, "entity_id": 11, "chunk_key": key_b.chunk_key},
        ]
    )
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    await index.upsert(
        [
            VectorRecord(key=key_a, values=(1.0, 0.0, 0.0, 0.0)),
            VectorRecord(key=key_b, values=(0.0, 1.0, 0.0, 0.0)),
        ]
    )

    insert_call = next(call for call in session.calls if "INSERT INTO" in call[0])
    assert insert_call[1] == {
        "project_id": 7,
        "chunk_id_0": 101,
        "embedding_0": "[1,0,0,0]",
        "dimensions_0": 4,
        "chunk_id_1": 102,
        "embedding_1": "[0,1,0,0]",
        "dimensions_1": 4,
    }
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_upsert_rejects_missing_manifest_key(monkeypatch) -> None:
    key = VectorKey(entity_id=12, chunk_key="entity:12:0")
    session = FakeSession()
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    with pytest.raises(RuntimeError, match="manifest rows are missing"):
        await index.upsert([VectorRecord(key=key, values=(1.0, 0.0, 0.0, 0.0))])


@pytest.mark.asyncio
async def test_delete_stable_keys_and_entity(monkeypatch) -> None:
    key = VectorKey(entity_id=13, chunk_key="entity:13:0")
    session = FakeSession(chunk_rows=[{"id": 103, "entity_id": 13, "chunk_key": key.chunk_key}])
    _install_session(monkeypatch, session)
    index = PgVectorIndex(MagicMock(), _scope())
    index._initialized = True

    await index.delete([])
    await index.delete([key])
    await index.delete_entity(13)
    await index.delete_orphans([key])

    delete_calls = [call for call in session.calls if "DELETE FROM" in call[0]]
    assert len(delete_calls) == 3
    assert delete_calls[0][1] == {"chunk_id_0": 103}
    assert delete_calls[1][1] == {"project_id": 7, "entity_id": 13}
    assert delete_calls[2][1] == {
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

    assert await index.search([], limit=5) == []
    assert await index.search([1.0, 0.0, 0.0, 0.0], limit=0) == []
    with pytest.raises(ValueError, match="expected 4, got 2"):
        await index.search([1.0, 0.0], limit=5)

    matches = await index.search([1.0, 0.0, 0.0, 0.0], limit=5)

    assert [(match.key.entity_id, match.similarity) for match in matches] == [
        (14, 1.0),
        (15, 0.0),
    ]
    search_call = next(call for call in session.calls if "AS similarity" in call[0])
    assert "c.entity_id ASC, c.chunk_key ASC" in search_call[0]
    assert search_call[1] == {
        "query": "[1,0,0,0]",
        "project_id": 7,
        "dimensions": 4,
        "embedding_identity": "stub:4",
        "limit": 5,
    }
