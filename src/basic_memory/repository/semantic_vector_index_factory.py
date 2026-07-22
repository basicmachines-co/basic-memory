"""Composition-root factory for built-in and extension vector indexes."""

from __future__ import annotations

import hashlib
from importlib.metadata import entry_points
from typing import Protocol

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.embedding_provider import (
    EmbeddingProvider,
    embedding_provider_identity,
)
from basic_memory.repository.semantic_errors import SemanticVectorIndexExtensionError
from basic_memory.repository.semantic_vector_index import (
    SEMANTIC_VECTOR_INDEX_ENTRY_POINT_GROUP,
    SemanticVectorIndex,
    VectorIndexScope,
)


class SemanticVectorIndexFactory(Protocol):
    """Factory signature exposed to separately installed extension packages."""

    def __call__(
        self,
        *,
        scope: VectorIndexScope,
        app_config: BasicMemoryConfig,
    ) -> SemanticVectorIndex: ...


def resolve_semantic_vector_index_name(
    app_config: BasicMemoryConfig,
    database_backend: DatabaseBackend,
) -> str:
    """Resolve the effective index while preserving sqlite-vec for local SQLite."""
    if database_backend == DatabaseBackend.SQLITE:
        return "sqlite-vec"
    return app_config.semantic_vector_index.strip().lower()


def semantic_embedding_identity(provider: EmbeddingProvider) -> str:
    """Return the same model identity used by manifest invalidation."""
    return f"{type(provider).__name__}:{embedding_provider_identity(provider)}"


def _database_namespace(app_config: BasicMemoryConfig) -> str:
    """Derive a stable, credential-free namespace from the authoritative database."""
    if app_config.database_url:
        url = make_url(app_config.database_url)
        locator = "|".join(
            [
                url.get_backend_name(),
                url.host or "",
                str(url.port or ""),
                url.database or "",
            ]
        )
    else:
        locator = str((app_config.data_dir_path / "memory.db").resolve())
    digest = hashlib.sha256(locator.encode("utf-8")).hexdigest()[:24]
    return f"basic-memory-{digest}"


def build_vector_index_scope(
    app_config: BasicMemoryConfig,
    provider: EmbeddingProvider,
    project_id: int,
) -> VectorIndexScope:
    """Build the explicit isolation contract handed to every vector adapter."""
    return VectorIndexScope(
        namespace=_database_namespace(app_config),
        project_id=project_id,
        embedding_identity=semantic_embedding_identity(provider),
        dimensions=provider.dimensions,
    )


def _load_extension_factory(name: str) -> SemanticVectorIndexFactory:
    matches = list(entry_points(group=SEMANTIC_VECTOR_INDEX_ENTRY_POINT_GROUP, name=name))
    if not matches:
        raise SemanticVectorIndexExtensionError(
            f"Semantic vector index '{name}' is configured but no extension is installed. "
            f"Install a package that provides the '{name}' entry point in "
            f"'{SEMANTIC_VECTOR_INDEX_ENTRY_POINT_GROUP}'."
        )
    if len(matches) > 1:
        providers = ", ".join(sorted(entry_point.value for entry_point in matches))
        raise SemanticVectorIndexExtensionError(
            f"Multiple semantic vector index extensions provide '{name}': {providers}."
        )

    loaded = matches[0].load()
    if not callable(loaded):
        raise SemanticVectorIndexExtensionError(
            f"Semantic vector index entry point '{name}' must load a callable factory."
        )
    return loaded


def create_semantic_vector_index(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
    app_config: BasicMemoryConfig,
    database_backend: DatabaseBackend,
    embedding_provider: EmbeddingProvider,
) -> tuple[str, SemanticVectorIndex]:
    """Create the selected built-in adapter or load one external extension."""
    name = resolve_semantic_vector_index_name(app_config, database_backend)
    scope = build_vector_index_scope(app_config, embedding_provider, project_id)

    if name == "sqlite-vec":
        from basic_memory.repository.sqlite_vec_index import SQLiteVecIndex

        return name, SQLiteVecIndex(session_maker, scope)
    if name == "pgvector":
        from basic_memory.repository.pgvector_index import PgVectorIndex

        return name, PgVectorIndex(session_maker, scope)

    factory = _load_extension_factory(name)
    index = factory(scope=scope, app_config=app_config)
    if not isinstance(index, SemanticVectorIndex):
        raise SemanticVectorIndexExtensionError(
            f"Semantic vector index extension '{name}' returned an incompatible adapter."
        )
    if index.scope != scope:
        raise SemanticVectorIndexExtensionError(
            f"Semantic vector index extension '{name}' returned an adapter for the wrong scope."
        )
    return name, index
