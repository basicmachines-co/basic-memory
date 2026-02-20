"""Fixtures for semantic search benchmark tests.

Provides a pgvector-enabled container, engine factories for both backends,
and a parameterized ``search_combo`` fixture that yields a configured
SearchService for each (backend, provider) combination.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.db import DatabaseType, engine_session_factory
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models.base import Base
from basic_memory.models.search import (
    CREATE_POSTGRES_SEARCH_INDEX_FTS,
    CREATE_POSTGRES_SEARCH_INDEX_METADATA,
    CREATE_POSTGRES_SEARCH_INDEX_PERMALINK,
    CREATE_POSTGRES_SEARCH_INDEX_TABLE,
    CREATE_SEARCH_INDEX,
)
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.services.file_service import FileService
from basic_memory.services.search_service import SearchService

# Load .env so OPENAI_API_KEY (and other keys) are available to providers
load_dotenv()


# --- Combo descriptor ---


@dataclass(frozen=True)
class SearchCombo:
    """Describes a (backend, provider) combination for benchmark parameterization."""

    name: str
    backend: DatabaseBackend
    provider_name: str | None  # None = FTS-only
    dimensions: int | None


# All combinations the suite covers
ALL_COMBOS = [
    SearchCombo("sqlite-fts", DatabaseBackend.SQLITE, None, None),
    SearchCombo("sqlite-fastembed", DatabaseBackend.SQLITE, "fastembed", 384),
    SearchCombo("postgres-fts", DatabaseBackend.POSTGRES, None, None),
    SearchCombo("postgres-fastembed", DatabaseBackend.POSTGRES, "fastembed", 384),
    SearchCombo("postgres-openai", DatabaseBackend.POSTGRES, "openai", 1536),
]


# --- Skip guards ---


def _docker_available() -> bool:
    """Check if Docker is available for testcontainers."""
    import shutil

    return shutil.which("docker") is not None


def _fastembed_available() -> bool:
    try:
        import fastembed  # noqa: F401

        return True
    except ImportError:
        return False


def _openai_key_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def skip_if_needed(combo: SearchCombo) -> None:
    """Skip the current test if the combo's requirements aren't met."""
    if combo.backend == DatabaseBackend.POSTGRES and not _docker_available():
        pytest.skip("Docker not available for Postgres testcontainer")

    if combo.provider_name == "fastembed" and not _fastembed_available():
        pytest.skip("fastembed not installed (install/update basic-memory)")

    if combo.provider_name == "openai":
        if not _fastembed_available():
            pytest.skip("semantic dependencies not installed")
        if not _openai_key_available():
            pytest.skip("OPENAI_API_KEY not set")


# --- pgvector container (session-scoped, independent of main test suite) ---


@pytest.fixture(scope="session")
def pgvector_container():
    """Session-scoped pgvector container for semantic benchmarks.

    Uses pgvector/pgvector:pg16 image to get the vector extension.
    Only starts if Docker is available; yields None otherwise.
    """
    if not _docker_available():
        yield None
        return

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


# --- Engine factories ---


@pytest_asyncio.fixture
async def sqlite_engine_factory(tmp_path):
    """Create a SQLite engine + session factory for benchmark use."""
    db_path = tmp_path / "bench.db"

    # Explicit config forces SQLite backend regardless of user's local config
    sqlite_config = BasicMemoryConfig(database_backend=DatabaseBackend.SQLITE)
    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM, config=sqlite_config) as (
        engine,
        session_maker,
    ):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with db.scoped_session(session_maker) as session:
            await session.execute(text("DROP TABLE IF EXISTS search_index"))
            await session.execute(CREATE_SEARCH_INDEX)
            await session.commit()

        yield engine, session_maker


@pytest_asyncio.fixture
async def postgres_engine_factory(pgvector_container):
    """Create a Postgres engine + session factory with pgvector extension."""
    if pgvector_container is None:
        yield None
        return

    sync_url = pgvector_container.get_connection_url()
    async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")

    engine = create_async_engine(async_url, echo=False, poolclass=NullPool)
    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Create schema from scratch for each test
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS search_vector_embeddings CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS search_vector_chunks CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS search_index CASCADE"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(CREATE_POSTGRES_SEARCH_INDEX_TABLE)
        await conn.execute(CREATE_POSTGRES_SEARCH_INDEX_FTS)
        await conn.execute(CREATE_POSTGRES_SEARCH_INDEX_METADATA)
        await conn.execute(CREATE_POSTGRES_SEARCH_INDEX_PERMALINK)

    yield engine, session_maker

    await engine.dispose()


# --- Embedding provider factories ---


def _create_fastembed_provider() -> EmbeddingProvider:
    from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider

    return FastEmbedEmbeddingProvider(model_name="bge-small-en-v1.5", batch_size=64)


def _create_openai_provider() -> EmbeddingProvider:
    from basic_memory.repository.openai_provider import OpenAIEmbeddingProvider

    return OpenAIEmbeddingProvider(model_name="text-embedding-3-small", dimensions=1536)


# --- Search service factory ---


async def create_search_service(
    engine_factory_result,
    combo: SearchCombo,
    tmp_path: Path,
    embedding_provider: EmbeddingProvider | None = None,
) -> SearchService:
    """Build a fully wired SearchService for a given combo."""
    engine, session_maker = engine_factory_result

    # Create test project
    project_repo = ProjectRepository(session_maker)
    project = await project_repo.create(
        {
            "name": "bench-project",
            "description": "Semantic benchmark project",
            "path": str(tmp_path),
            "is_active": True,
            "is_default": True,
        }
    )

    # Build app config
    semantic_enabled = combo.provider_name is not None
    app_config = BasicMemoryConfig(
        env="test",
        projects={"bench-project": str(tmp_path)},
        default_project="bench-project",
        database_backend=combo.backend,
        semantic_search_enabled=semantic_enabled,
    )

    # Create search repository (backend-specific)
    if combo.backend == DatabaseBackend.POSTGRES:
        from basic_memory.repository.postgres_search_repository import PostgresSearchRepository

        search_repo: SearchRepository = PostgresSearchRepository(
            session_maker,
            project_id=project.id,
            app_config=app_config,
            embedding_provider=embedding_provider,
        )
    else:
        from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

        repo = SQLiteSearchRepository(
            session_maker,
            project_id=project.id,
            app_config=app_config,
        )
        # Inject provider directly for SQLite
        if embedding_provider is not None:
            repo._semantic_enabled = True
            repo._embedding_provider = embedding_provider
            repo._vector_dimensions = embedding_provider.dimensions
            repo._vector_tables_initialized = False
        search_repo = repo

    entity_repo = EntityRepository(session_maker, project_id=project.id)
    entity_parser = EntityParser(tmp_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(tmp_path, markdown_processor)

    service = SearchService(search_repo, entity_repo, file_service)
    await service.init_search_index()
    return service
