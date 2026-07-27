"""Contract and composition tests for semantic vector indexes."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
)
from basic_memory.repository.semantic_vector_index import (
    SemanticVectorIndex,
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
    validate_query_dimensions,
    validate_vector_dimensions,
)
from basic_memory.repository.semantic_vector_index_factory import (
    build_vector_index_scope,
    create_semantic_vector_index,
    resolve_semantic_vector_index_name,
)


class StubEmbeddingProvider:
    """Small embedding provider used only to build deterministic scopes."""

    model_name = "stub-model"
    dimensions = 3

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


class StubVectorIndex:
    """Structurally complete adapter for runtime protocol checks."""

    def __init__(self, scope: VectorIndexScope):
        self.scope = scope

    async def initialize(self) -> None:
        return None

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        return None

    async def delete(self, records: Sequence[VectorDeletion]) -> None:
        return None

    async def delete_entity(self, entity_id: int) -> None:
        return None

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
    ) -> list[VectorMatch]:
        return []


def _postgres_config(**overrides: object) -> BasicMemoryConfig:
    values: dict[str, object] = {
        "env": "test",
        "database_backend": DatabaseBackend.POSTGRES,
        "database_url": "postgresql+asyncpg://user:secret@db.example.test:5432/memory",
        "semantic_search_enabled": True,
        "semantic_vector_index": "pgvector",
    }
    values.update(overrides)
    return BasicMemoryConfig(**values)


def test_vector_contract_values_and_dimension_validation() -> None:
    scope = VectorIndexScope(
        namespace="basic-memory-test",
        project_id=7,
        embedding_identity="stub:3",
        dimensions=3,
    )
    key = VectorKey(entity_id=11, chunk_key="entity:11:0")
    record = VectorRecord(key=key, source_hash="hash", values=(1.0, 0.0, 0.0))

    assert isinstance(StubVectorIndex(scope), SemanticVectorIndex)
    validate_vector_dimensions(scope, [record])
    validate_query_dimensions(scope, [1.0, 0.0, 0.0])

    with pytest.raises(ValueError, match="expected 3, got 2"):
        validate_vector_dimensions(
            scope,
            [VectorRecord(key=key, source_hash="hash", values=(1.0, 0.0))],
        )
    with pytest.raises(ValueError, match="expected 3, got 1"):
        validate_query_dimensions(scope, [1.0])


def test_selector_defaults_to_pgvector_and_sqlite_remains_automatic() -> None:
    default_config = BasicMemoryConfig(env="test")
    postgres_config = _postgres_config()

    assert default_config.semantic_vector_index == "pgvector"
    assert (
        resolve_semantic_vector_index_name(default_config, DatabaseBackend.POSTGRES) == "pgvector"
    )
    assert (
        resolve_semantic_vector_index_name(postgres_config, DatabaseBackend.SQLITE) == "sqlite-vec"
    )
    with pytest.raises(ValidationError, match="semantic_vector_index"):
        _postgres_config(semantic_vector_index="test-extension")


def test_scope_is_stable_credential_free_and_project_isolated() -> None:
    provider: EmbeddingProvider = StubEmbeddingProvider()
    first = build_vector_index_scope(_postgres_config(), provider, project_id=7)
    rotated_password = build_vector_index_scope(
        _postgres_config(
            database_url=(
                "postgresql+asyncpg://user:new-secret@db.example.test:5432/memory?sslmode=require"
            )
        ),
        provider,
        project_id=7,
    )
    other_project = build_vector_index_scope(_postgres_config(), provider, project_id=8)
    other_user = build_vector_index_scope(
        _postgres_config(
            database_url="postgresql+asyncpg://tenant-user:secret@db.example.test:5432/memory"
        ),
        provider,
        project_id=7,
    )
    other_schema = build_vector_index_scope(
        _postgres_config(
            database_url=(
                "postgresql+asyncpg://user:secret@db.example.test:5432/memory"
                "?options=-csearch_path%3Dtenant"
            )
        ),
        provider,
        project_id=7,
    )
    first_socket = build_vector_index_scope(
        _postgres_config(
            database_url=(
                "postgresql+asyncpg://user:secret@/memory?host=%2Fvar%2Frun%2Fpostgresql&port=5432"
            )
        ),
        provider,
        project_id=7,
    )
    other_socket = build_vector_index_scope(
        _postgres_config(
            database_url=(
                "postgresql+asyncpg://user:secret@/memory?host=%2Ftmp%2Fpostgresql&port=5433"
            )
        ),
        provider,
        project_id=7,
    )

    assert first.namespace == rotated_password.namespace
    assert "secret" not in first.namespace
    assert first.project_id != other_project.project_id
    assert first.namespace != other_user.namespace
    assert first.namespace != other_schema.namespace
    assert first_socket.namespace != other_socket.namespace
    assert first.embedding_identity == "StubEmbeddingProvider:stub-model:3"
    assert first.dimensions == 3
    assert first.storage_key == rotated_password.storage_key


def test_milvus_without_optional_dependencies_reports_install_extra(monkeypatch) -> None:
    config = _postgres_config(
        semantic_vector_index="milvus",
        milvus_uri="https://zilliz.example",
    )
    real_import = builtins.__import__

    def import_without_pymilvus(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "basic_memory.repository.milvus_index":
            raise ModuleNotFoundError("No module named 'pymilvus'", name="pymilvus")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_pymilvus)

    with pytest.raises(
        SemanticDependenciesMissingError,
        match=r"basic-memory\[milvus\]",
    ):
        create_semantic_vector_index(
            session_maker=MagicMock(),
            project_id=7,
            app_config=config,
            database_backend=DatabaseBackend.POSTGRES,
            embedding_provider=StubEmbeddingProvider(),
        )


def test_search_repository_composition_root_injects_selected_adapter(monkeypatch) -> None:
    provider = StubEmbeddingProvider()
    scope = build_vector_index_scope(_postgres_config(), provider, project_id=7)
    index = StubVectorIndex(scope)
    monkeypatch.setattr(
        "basic_memory.repository.search_repository.create_embedding_provider",
        lambda _config: provider,
    )
    monkeypatch.setattr(
        "basic_memory.repository.search_repository.create_semantic_vector_index",
        lambda **_kwargs: ("milvus", index),
    )

    repository = create_search_repository(
        MagicMock(),
        project_id=7,
        app_config=_postgres_config(semantic_vector_index="milvus"),
        database_backend=DatabaseBackend.POSTGRES,
    )

    assert isinstance(repository, PostgresSearchRepository)
    assert repository._semantic_vector_index_name == "milvus"
    assert repository._semantic_vector_index is index


def test_disabled_search_repository_retains_configured_adapter_name(monkeypatch) -> None:
    """Cleanup must identify external ownership without loading an embedding model."""
    monkeypatch.setattr(
        "basic_memory.repository.search_repository.create_embedding_provider",
        lambda _config: pytest.fail("disabled search must not create an embedding provider"),
    )

    repository = create_search_repository(
        MagicMock(),
        project_id=7,
        app_config=_postgres_config(
            semantic_search_enabled=False,
            semantic_vector_index="milvus",
        ),
        database_backend=DatabaseBackend.POSTGRES,
    )

    assert isinstance(repository, PostgresSearchRepository)
    assert repository._semantic_vector_index_name == "milvus"
    assert not hasattr(repository, "_semantic_vector_index")
