"""Composing a SearchReader over a scope without a project repository."""

from typing import Any
from unittest.mock import MagicMock

import pytest

import basic_memory.repository.search_repository as search_repository_module
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.postgres_search_query import PostgresFts
from basic_memory.repository.search_reader import Reranking
from basic_memory.repository.search_repository import create_search_reader
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_vector_index_factory import semantic_embedding_identity
from basic_memory.repository.sqlite_search_query import SQLiteFts

SCOPE = ProjectScope.of([3, 1])


class _StubEmbeddingProvider:
    model_name = "stub"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


class _StubReranker:
    model_name = "stub-reranker"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [0.5 for _ in documents]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


def _config(backend: DatabaseBackend, **overrides: object) -> BasicMemoryConfig:
    return BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/test"},
        default_project="test-project",
        database_backend=backend,
        **overrides,
    )


@pytest.mark.parametrize(
    ("backend", "fts_type"),
    [(DatabaseBackend.SQLITE, SQLiteFts), (DatabaseBackend.POSTGRES, PostgresFts)],
)
def test_reader_without_semantic_search_is_full_text_only(monkeypatch, backend, fts_type):
    """Disabled semantic search never resolves a provider, and the engine picks the backend."""
    monkeypatch.setattr(
        search_repository_module,
        "create_embedding_provider",
        lambda _config: pytest.fail("a full-text reader must not load an embedding provider"),
    )

    reader = create_search_reader(
        MagicMock(), SCOPE, _config(backend, semantic_search_enabled=False)
    )

    assert reader.scope == SCOPE
    assert reader.semantic is None
    assert isinstance(reader.fts, fts_type)


@pytest.mark.parametrize("reranker", [None, _StubReranker()])
def test_reader_with_semantic_search_composes_the_shared_stack(monkeypatch, reranker):
    """The reader gets the same provider, adapter, and reranker a project repository would."""
    provider = _StubEmbeddingProvider()
    index = MagicMock()
    captured: dict[str, Any] = {}

    def fake_create_index(**kwargs: Any) -> tuple[str, Any]:
        captured.update(kwargs)
        return "sqlite-vec", index

    monkeypatch.setattr(search_repository_module, "create_embedding_provider", lambda _c: provider)
    monkeypatch.setattr(search_repository_module, "create_semantic_vector_index", fake_create_index)
    monkeypatch.setattr(search_repository_module, "create_rerank_provider", lambda _c: reranker)
    config = _config(
        DatabaseBackend.SQLITE,
        semantic_search_enabled=True,
        semantic_vector_k=7,
        semantic_min_similarity=0.25,
        reranker_candidates=9,
        reranker_max_document_chars=123,
    )

    reader = create_search_reader(MagicMock(), SCOPE, config)

    semantic = reader.semantic
    assert semantic is not None
    assert semantic.scope == SCOPE and semantic.fts is reader.fts
    assert semantic.vector.index is index
    assert semantic.vector.index_name == "sqlite-vec"
    assert semantic.vector.embedding_provider is provider
    assert semantic.vector.embedding_model == semantic_embedding_identity(provider)
    assert (semantic.vector.vector_k, semantic.vector.min_similarity) == (7, 0.25)
    # The adapter is built for the database, not for a project.
    assert captured["database_backend"] == DatabaseBackend.SQLITE
    assert captured["embedding_provider"] is provider
    assert "project_id" not in captured
    if reranker is None:
        assert semantic.rerank is None
    else:
        assert semantic.rerank == Reranking(provider=reranker, candidates=9, max_document_chars=123)
