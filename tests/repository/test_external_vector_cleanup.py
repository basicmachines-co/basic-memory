"""Tests for DB-first cleanup of externally stored semantic vectors."""

from collections.abc import Sequence
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository.accepted_note_vector_cleanup import (
    delete_project_index_vector_rows,
)
from basic_memory.repository.semantic_errors import SemanticVectorIndexExtensionError


class _ScalarResult:
    def __init__(self, values: Sequence[object] = ()) -> None:
        self._values = values

    def scalars(self) -> Sequence[object]:
        return self._values


class _PostgresSession:
    def __init__(self, results: Sequence[_ScalarResult]) -> None:
        self._results = iter(results)
        self.executed: list[tuple[str, dict[str, object]]] = []
        self._bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()

    def get_bind(self) -> object:
        return self._bind

    async def execute(self, statement: object, params: dict[str, object] | None = None) -> Any:
        self.executed.append((str(statement), params or {}))
        return next(self._results)


class _RecordingExternalCleaner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[int, ...], frozenset[str]]] = []

    async def delete_external_entity_vectors(
        self,
        entity_ids: Sequence[int],
        *,
        vector_index_names: frozenset[str],
    ) -> None:
        self.calls.append((tuple(entity_ids), vector_index_names))


@pytest.mark.asyncio
async def test_external_vectors_are_deleted_before_their_manifest() -> None:
    session = _PostgresSession(
        [
            _ScalarResult(["search_vector_chunks"]),
            _ScalarResult(["milvus"]),
            _ScalarResult(),
        ]
    )
    cleaner = _RecordingExternalCleaner()

    await delete_project_index_vector_rows(
        cast(AsyncSession, session),
        project_id=7,
        entity_ids=[41, 42],
        external_vector_cleaner=cleaner,
    )

    assert cleaner.calls == [((41, 42), frozenset({"milvus"}))]
    assert str(session.executed[-1][0]).lstrip().startswith("DELETE FROM search_vector_chunks")


@pytest.mark.asyncio
async def test_external_vector_manifest_is_preserved_without_a_cleaner() -> None:
    session = _PostgresSession(
        [
            _ScalarResult(["search_vector_chunks"]),
            _ScalarResult(["milvus"]),
        ]
    )

    with pytest.raises(SemanticVectorIndexExtensionError, match="project-scoped"):
        await delete_project_index_vector_rows(
            cast(AsyncSession, session),
            project_id=7,
            entity_ids=[41],
        )

    assert not any(
        statement.lstrip().startswith("DELETE FROM search_vector_chunks")
        for statement, _params in session.executed
    )
