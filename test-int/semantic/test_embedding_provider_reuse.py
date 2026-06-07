"""Integration tests for process-wide embedding provider reuse (#872).

A long-running ``basic-memory mcp`` server constructs a new search repository per
request, per sync batch, and per project. Each repository used to derive its own
embedding provider via ``create_embedding_provider()``. If the provider cache key
ever drifted, that reloaded the ~2.3GB FastEmbed/ONNX model and leaked memory in
onnxruntime's CPU arena (which never returns memory to the OS).

These tests use the *real* composition paths — ``create_embedding_provider``, the
``create_search_repository`` factory, and the FastAPI deps function
``get_search_repository`` — with a real FastEmbed provider. FastEmbed loads the
ONNX model lazily on first embed, so constructing providers/repositories here is
cheap and never touches the native model.

They deliberately use the semantic suite's ``sqlite_engine_factory`` (not the
parent ``engine_factory``): the parent fixture depends on ``postgres_engine``,
which this directory overrides to spin up a Docker testcontainer gated only by a
``docker`` binary on PATH. On Windows CI that binary exists without a usable
daemon, so requesting the parent factory aborts these SQLite-only tests with a
testcontainers Ryuk error (#872 follow-up). Staying on the SQLite factory keeps
them backend-correct and Docker-free.
"""

from __future__ import annotations

from typing import cast

import pytest

from basic_memory.config import BasicMemoryConfig, DatabaseBackend, ProjectEntry
from basic_memory.deps.repositories import get_search_repository
from basic_memory.repository.embedding_provider_factory import (
    create_embedding_provider,
    reset_embedding_provider_cache,
)
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository


def _semantic_config(project_path) -> BasicMemoryConfig:
    """Build a semantic-enabled FastEmbed config rooted at the test path."""
    return BasicMemoryConfig(
        env="test",
        projects={"test-project": ProjectEntry(path=str(project_path))},
        default_project="test-project",
        database_backend=DatabaseBackend.SQLITE,
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
    )


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    reset_embedding_provider_cache()
    yield
    reset_embedding_provider_cache()


@pytest.mark.asyncio
async def test_factory_resolves_single_provider_across_repositories(
    tmp_path, sqlite_engine_factory
):
    """The factory must inject one cached provider into every search repository."""
    _engine, session_maker = sqlite_engine_factory
    config = _semantic_config(tmp_path)

    expected_provider = create_embedding_provider(config)
    assert isinstance(expected_provider, FastEmbedEmbeddingProvider)

    # Two repositories, mimicking per-request / per-sync construction.
    repo_a = cast(
        SQLiteSearchRepository,
        create_search_repository(session_maker, project_id=1, app_config=config),
    )
    repo_b = cast(
        SQLiteSearchRepository,
        create_search_repository(session_maker, project_id=2, app_config=config),
    )

    # Both repos reuse the exact same cached provider object — no second model load.
    assert repo_a._embedding_provider is expected_provider
    assert repo_b._embedding_provider is expected_provider


@pytest.mark.asyncio
async def test_deps_path_reuses_cached_provider(tmp_path, sqlite_engine_factory):
    """The real FastAPI deps function must reuse the cached provider, not rebuild it."""
    _engine, session_maker = sqlite_engine_factory
    config = _semantic_config(tmp_path)

    expected_provider = create_embedding_provider(config)

    repo = cast(
        SQLiteSearchRepository,
        await get_search_repository(
            session_maker=session_maker,
            project_id=1,
            app_config=config,
        ),
    )

    assert repo._embedding_provider is expected_provider


@pytest.mark.asyncio
async def test_factory_skips_provider_when_semantic_disabled(tmp_path, sqlite_engine_factory):
    """With semantic search off, no provider is created and none is injected."""
    _engine, session_maker = sqlite_engine_factory
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": ProjectEntry(path=str(tmp_path))},
        default_project="test-project",
        database_backend=DatabaseBackend.SQLITE,
        semantic_search_enabled=False,
    )

    repo = cast(
        SQLiteSearchRepository,
        create_search_repository(session_maker, project_id=1, app_config=config),
    )

    assert repo._embedding_provider is None
