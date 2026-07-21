"""Tests for the reranker provider factory."""

import pytest
from pydantic import ValidationError

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.fastembed_rerank_provider import FastEmbedRerankProvider
from basic_memory.repository.litellm_rerank_provider import LiteLLMRerankProvider
import basic_memory.repository.rerank_provider_factory as factory
from basic_memory.repository.rerank_provider_factory import (
    create_rerank_provider,
    reset_rerank_provider_cache,
)


def _config(**overrides) -> BasicMemoryConfig:
    base = dict(
        env="test",
        projects={"test-project": "/tmp/test"},
        default_project="test-project",
        database_backend=DatabaseBackend.SQLITE,
    )
    base.update(overrides)
    return BasicMemoryConfig(**base)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_rerank_provider_cache()
    yield
    reset_rerank_provider_cache()


def test_disabled_returns_none():
    """Reranking is off by default; the factory yields no provider."""
    assert create_rerank_provider(_config(reranker_enabled=False)) is None


def test_fastembed_provider_selected_with_resolved_cache_dir():
    config = _config(
        reranker_enabled=True,
        reranker_provider="fastembed",
        reranker_model="Xenova/ms-marco-MiniLM-L-6-v2",
        semantic_embedding_cache_dir="/tmp/fastembed-cache",
    )
    provider = create_rerank_provider(config)
    assert isinstance(provider, FastEmbedRerankProvider)
    assert provider.model_name == "Xenova/ms-marco-MiniLM-L-6-v2"
    # Reranker shares the embedding provider's resolved cache dir.
    assert provider.cache_dir == "/tmp/fastembed-cache"


def test_litellm_provider_selected_with_routing():
    config = _config(
        reranker_enabled=True,
        reranker_provider="litellm",
        reranker_model="cohere/rerank-v3.5",
        reranker_api_key="secret",
        reranker_api_base="https://rerank.example",
    )
    provider = create_rerank_provider(config)
    assert isinstance(provider, LiteLLMRerankProvider)
    assert provider.model_name == "cohere/rerank-v3.5"
    assert provider._api_key == "secret"
    assert provider._api_base == "https://rerank.example"


def test_unsupported_provider_raises():
    with pytest.raises(ValueError, match="Unsupported reranker provider: nope"):
        create_rerank_provider(_config(reranker_enabled=True, reranker_provider="nope"))


def test_same_config_returns_cached_singleton():
    config = _config(reranker_enabled=True, reranker_provider="fastembed")
    first = create_rerank_provider(config)
    second = create_rerank_provider(config)
    assert first is second


def test_distinct_config_creates_second_provider():
    """A second distinct key builds a new provider (and warns about the reload)."""
    first = create_rerank_provider(_config(reranker_enabled=True, reranker_model="model-a"))
    second = create_rerank_provider(_config(reranker_enabled=True, reranker_model="model-b"))
    assert first is not second


def test_distinct_cache_dir_does_not_collide():
    """Two configs differing only in cache dir must not share one singleton (#741/#872)."""
    a = create_rerank_provider(
        _config(reranker_enabled=True, semantic_embedding_cache_dir="/tmp/rr-a")
    )
    b = create_rerank_provider(
        _config(reranker_enabled=True, semantic_embedding_cache_dir="/tmp/rr-b")
    )
    assert a is not b
    assert isinstance(a, FastEmbedRerankProvider) and isinstance(b, FastEmbedRerankProvider)
    assert a.cache_dir == "/tmp/rr-a"
    assert b.cache_dir == "/tmp/rr-b"


def test_reranker_enabled_requires_semantic_search():
    """Config rejects reranking without semantic search rather than silently no-op'ing."""
    with pytest.raises(ValidationError, match="requires semantic_search_enabled"):
        _config(reranker_enabled=True, semantic_search_enabled=False)


def test_litellm_provider_rejects_default_fastembed_model():
    """Selecting litellm without overriding the model is a footgun; reject it at config."""
    with pytest.raises(ValidationError, match="requires an explicit reranker_model"):
        _config(reranker_enabled=True, reranker_provider="litellm")


def test_litellm_provider_accepts_explicit_model():
    """An explicit provider/model model id passes and reaches the provider."""
    provider = create_rerank_provider(
        _config(
            reranker_enabled=True,
            reranker_provider="litellm",
            reranker_model="cohere/rerank-v3.5",
        )
    )
    assert isinstance(provider, LiteLLMRerankProvider)
    assert provider.model_name == "cohere/rerank-v3.5"


def test_reset_clears_cache():
    config = _config(reranker_enabled=True, reranker_provider="fastembed")
    first = create_rerank_provider(config)
    reset_rerank_provider_cache()
    second = create_rerank_provider(config)
    assert first is not second


def test_concurrent_race_returns_winning_provider(monkeypatch):
    """The double-checked lock returns the racing writer's provider, not ours.

    Simulates a concurrent caller that populated the cache after our first miss
    but before we take the write lock: the second in-lock check must win.
    """
    winner = object()

    class _RacyCache(dict):
        def __init__(self):
            super().__init__()
            self._gets = 0

        def get(self, key, default=None):
            # First check (outside lock) misses so we build; second check (in lock)
            # finds the winner another thread inserted mid-flight.
            self._gets += 1
            return winner if self._gets > 1 else None

    monkeypatch.setattr(factory, "_RERANK_PROVIDER_CACHE", _RacyCache())
    result = create_rerank_provider(_config(reranker_enabled=True, reranker_provider="fastembed"))
    assert result is winner
