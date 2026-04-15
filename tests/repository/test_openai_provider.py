"""Tests for OpenAIEmbeddingProvider and embedding provider factory."""

import asyncio
import builtins
import sys
from types import SimpleNamespace

import pytest

from basic_memory.config import BasicMemoryConfig
import basic_memory.repository.embedding_provider_factory as embedding_provider_factory_module
from basic_memory.repository.embedding_provider_factory import (
    create_embedding_provider,
    reset_embedding_provider_cache,
)
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.openai_provider import OpenAIEmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


class _StubEmbeddingsApi:
    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    async def create(self, *, model: str, input: list[str]):
        self.calls.append((model, input))
        vectors = []
        for index, value in enumerate(input):
            base = float(len(value))
            vectors.append(SimpleNamespace(index=index, embedding=[base, base + 1.0, base + 2.0]))
        return SimpleNamespace(data=vectors)


class _StubAsyncOpenAI:
    init_count = 0

    def __init__(self, *, api_key: str, base_url=None, timeout=30.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.embeddings = _StubEmbeddingsApi()
        _StubAsyncOpenAI.init_count += 1


class _ConcurrentEmbeddingsApi:
    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def create(self, *, model: str, input: list[str]):
        self.calls.append((model, input))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.05)
            vectors = []
            for index, value in enumerate(input):
                base = float(len(value))
                vectors.append(
                    SimpleNamespace(index=index, embedding=[base, base + 1.0, base + 2.0])
                )
            return SimpleNamespace(data=vectors)
        finally:
            self.in_flight -= 1


class _MalformedEmbeddingsApi:
    async def create(self, *, model: str, input: list[str]):
        return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0, 2.0, 3.0])])


@pytest.fixture(autouse=True)
def _reset_embedding_provider_cache_fixture():
    reset_embedding_provider_cache()
    yield
    reset_embedding_provider_cache()


@pytest.mark.asyncio
async def test_openai_provider_lazy_loads_and_reuses_client(monkeypatch):
    """Provider should instantiate AsyncOpenAI lazily and reuse a single client."""
    module = type(sys)("openai")
    setattr(module, "AsyncOpenAI", _StubAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _StubAsyncOpenAI.init_count = 0

    provider = OpenAIEmbeddingProvider(
        model_name="text-embedding-3-small", batch_size=2, dimensions=3
    )
    assert provider._client is None

    first = await provider.embed_query("auth query")
    second = await provider.embed_documents(["queue task", "relation sync"])

    assert _StubAsyncOpenAI.init_count == 1
    assert provider._client is not None
    assert len(first) == 3
    assert len(second) == 2
    assert len(second[0]) == 3


@pytest.mark.asyncio
async def test_openai_provider_dimension_mismatch_raises_error(monkeypatch):
    """Provider should fail fast when response dimensions differ from configured dimensions."""
    module = type(sys)("openai")
    setattr(module, "AsyncOpenAI", _StubAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIEmbeddingProvider(dimensions=2)
    with pytest.raises(RuntimeError, match="3-dimensional vectors"):
        await provider.embed_documents(["semantic note"])


@pytest.mark.asyncio
async def test_openai_provider_missing_dependency_raises_actionable_error(monkeypatch):
    """Missing openai package should raise SemanticDependenciesMissingError."""
    monkeypatch.delitem(sys.modules, "openai", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai":
            raise ImportError("openai not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    provider = OpenAIEmbeddingProvider(model_name="text-embedding-3-small")
    with pytest.raises(SemanticDependenciesMissingError) as error:
        await provider.embed_query("test")

    assert "pip install -U basic-memory" in str(error.value)


@pytest.mark.asyncio
async def test_openai_provider_missing_api_key_raises_error(monkeypatch):
    """OPENAI_API_KEY is required unless api_key is passed explicitly."""
    module = type(sys)("openai")
    setattr(module, "AsyncOpenAI", _StubAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    provider = OpenAIEmbeddingProvider(model_name="text-embedding-3-small")
    with pytest.raises(SemanticDependenciesMissingError) as error:
        await provider.embed_query("test")

    assert "OPENAI_API_KEY" in str(error.value)


def test_embedding_provider_factory_selects_fastembed_by_default():
    """Factory should select fastembed when provider is configured as fastembed."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, FastEmbedEmbeddingProvider)


def test_embedding_provider_factory_selects_openai_and_applies_default_model():
    """Factory should map local default model to OpenAI default when provider is openai."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="openai",
        semantic_embedding_model="bge-small-en-v1.5",
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.model_name == "text-embedding-3-small"


def test_embedding_provider_factory_rejects_unknown_provider():
    """Factory should fail fast for unsupported provider names."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="unknown-provider",
    )
    with pytest.raises(ValueError):
        create_embedding_provider(config)


def test_embedding_provider_factory_passes_custom_dimensions_to_fastembed():
    """Factory should forward semantic_embedding_dimensions to FastEmbed provider."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_dimensions=768,
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.dimensions == 768


def test_embedding_provider_factory_passes_custom_dimensions_to_openai():
    """Factory should forward semantic_embedding_dimensions to OpenAI provider."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="openai",
        semantic_embedding_dimensions=3072,
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.dimensions == 3072


def test_embedding_provider_factory_uses_provider_defaults_when_dimensions_not_set():
    """Factory should use provider defaults (384/1536) when dimensions is None."""
    fastembed_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
    )
    fastembed_provider = create_embedding_provider(fastembed_config)
    assert isinstance(fastembed_provider, FastEmbedEmbeddingProvider)
    assert fastembed_provider.dimensions == 384

    openai_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="openai",
    )
    openai_provider = create_embedding_provider(openai_config)
    assert isinstance(openai_provider, OpenAIEmbeddingProvider)
    assert openai_provider.dimensions == 1536


def test_embedding_provider_factory_forwards_fastembed_runtime_knobs():
    """Factory should forward FastEmbed runtime tuning config fields."""
    reset_embedding_provider_cache()
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_cache_dir="/tmp/fastembed-cache",
        semantic_embedding_threads=3,
        semantic_embedding_parallel=2,
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.cache_dir == "/tmp/fastembed-cache"
    assert provider.threads == 3
    assert provider.parallel == 2


def test_embedding_provider_factory_uses_default_cache_dir_when_unset(config_home, monkeypatch):
    """Factory should pass the data-dir-relative default when cache_dir is None.

    Legacy configs that carry an explicit ``semantic_embedding_cache_dir: null``
    must still get a user-writable cache path rather than letting FastEmbed fall
    back to ``<tmp>/fastembed_cache``. See #741.
    """
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    reset_embedding_provider_cache()

    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": str(config_home / "project")},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_cache_dir=None,
    )

    provider = create_embedding_provider(config)
    assert isinstance(provider, FastEmbedEmbeddingProvider)
    expected = str(config_home / ".basic-memory" / "fastembed_cache")
    assert provider.cache_dir == expected


def test_embedding_provider_factory_cache_key_reflects_resolved_cache_dir(
    config_home, tmp_path, monkeypatch
):
    """Changing FASTEMBED_CACHE_PATH must yield a distinct cached provider.

    The provider cache key uses the *resolved* cache dir rather than the raw
    (nullable) config field, so env-driven path changes invalidate the cache
    instead of silently returning a stale provider pointing at the old path.
    """
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    reset_embedding_provider_cache()

    base_kwargs = dict(
        env="test",
        projects={"test-project": str(config_home / "project")},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_cache_dir=None,
    )

    provider_a = create_embedding_provider(BasicMemoryConfig(**base_kwargs))
    assert isinstance(provider_a, FastEmbedEmbeddingProvider)

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "alt-cache"))
    provider_b = create_embedding_provider(BasicMemoryConfig(**base_kwargs))

    assert isinstance(provider_b, FastEmbedEmbeddingProvider)
    assert provider_b is not provider_a
    assert provider_a.cache_dir == str(config_home / ".basic-memory" / "fastembed_cache")
    assert provider_b.cache_dir == str(tmp_path / "alt-cache")


def test_fastembed_provider_reports_runtime_log_attrs():
    """FastEmbed should expose the resolved runtime knobs for batch startup logs."""
    provider = FastEmbedEmbeddingProvider(batch_size=128, threads=4, parallel=2)

    assert provider.runtime_log_attrs() == {
        "provider_batch_size": 128,
        "threads": 4,
        "configured_parallel": 2,
        "effective_parallel": 2,
    }


def test_openai_provider_reports_runtime_log_attrs():
    """OpenAI provider should expose API batch fan-out settings for startup logs."""
    provider = OpenAIEmbeddingProvider(batch_size=32, request_concurrency=6)

    assert provider.runtime_log_attrs() == {
        "provider_batch_size": 32,
        "request_concurrency": 6,
    }


def test_embedding_provider_factory_auto_tunes_fastembed_runtime_knobs_from_cpu_budget(monkeypatch):
    """Unset FastEmbed runtime knobs should resolve from available CPU budget."""
    monkeypatch.setattr(embedding_provider_factory_module.os, "process_cpu_count", lambda: 8)
    monkeypatch.setattr(embedding_provider_factory_module.os, "cpu_count", lambda: 8)

    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=None,
        semantic_embedding_parallel=None,
    )

    provider = create_embedding_provider(config)

    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.threads == 6
    assert provider.parallel == 1


def test_embedding_provider_factory_auto_tuning_caps_large_cpu_budgets(monkeypatch):
    """Large workers should still leave some headroom and stop at the thread cap."""
    monkeypatch.setattr(embedding_provider_factory_module.os, "process_cpu_count", lambda: 16)
    monkeypatch.setattr(embedding_provider_factory_module.os, "cpu_count", lambda: 16)

    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=None,
        semantic_embedding_parallel=None,
    )

    provider = create_embedding_provider(config)

    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.threads == 8
    assert provider.parallel == 1


def test_embedding_provider_factory_auto_tuning_stays_conservative_on_small_cpu_budget(
    monkeypatch,
):
    """Small workers should not get an oversized FastEmbed runtime footprint."""
    monkeypatch.setattr(embedding_provider_factory_module.os, "process_cpu_count", lambda: 2)
    monkeypatch.setattr(embedding_provider_factory_module.os, "cpu_count", lambda: 2)

    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=None,
        semantic_embedding_parallel=None,
    )

    provider = create_embedding_provider(config)

    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.threads == 2
    assert provider.parallel == 1


def test_embedding_provider_factory_reuses_provider_for_same_cache_key():
    """Factory should reuse the same provider instance for identical config values."""
    config_a = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=2,
    )
    config_b = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=2,
    )

    provider_a = create_embedding_provider(config_a)
    provider_b = create_embedding_provider(config_b)

    assert provider_a is provider_b


def test_embedding_provider_factory_reuses_auto_tuned_provider_for_same_cpu_budget(monkeypatch):
    """Auto-tuned FastEmbed providers should still reuse the process cache."""
    monkeypatch.setattr(embedding_provider_factory_module.os, "process_cpu_count", lambda: 8)
    monkeypatch.setattr(embedding_provider_factory_module.os, "cpu_count", lambda: 8)

    config_a = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=None,
        semantic_embedding_parallel=None,
    )
    config_b = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=None,
        semantic_embedding_parallel=None,
    )

    provider_a = create_embedding_provider(config_a)
    provider_b = create_embedding_provider(config_b)

    assert provider_a is provider_b


@pytest.mark.asyncio
async def test_openai_provider_runs_batches_concurrently_and_preserves_output_order(monkeypatch):
    """Concurrent request fan-out should keep batch order stable."""

    shared_api = _ConcurrentEmbeddingsApi()

    class _ConcurrentAsyncOpenAI:
        def __init__(self, *, api_key: str, base_url=None, timeout=30.0):
            self.embeddings = shared_api

    module = type(sys)("openai")
    setattr(module, "AsyncOpenAI", _ConcurrentAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIEmbeddingProvider(
        model_name="text-embedding-3-small",
        batch_size=2,
        request_concurrency=2,
        dimensions=3,
    )

    vectors = await provider.embed_documents(["a", "bbbb", "ccc", "dd"])

    assert shared_api.max_in_flight >= 2
    assert vectors == [
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
        [3.0, 4.0, 5.0],
        [2.0, 3.0, 4.0],
    ]


@pytest.mark.asyncio
async def test_openai_provider_fails_fast_on_malformed_concurrent_batch(monkeypatch):
    """Missing batch indexes should still raise even when requests run concurrently."""

    class _MalformedAsyncOpenAI:
        def __init__(self, *, api_key: str, base_url=None, timeout=30.0):
            self.embeddings = _MalformedEmbeddingsApi()

    module = type(sys)("openai")
    setattr(module, "AsyncOpenAI", _MalformedAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIEmbeddingProvider(batch_size=2, request_concurrency=2, dimensions=3)
    with pytest.raises(RuntimeError, match="missing expected vector index"):
        await provider.embed_documents(["one", "two", "three", "four"])


def test_embedding_provider_factory_creates_new_provider_for_different_cache_key():
    """Factory should create distinct providers when cache key fields differ."""
    config_a = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=2,
    )
    config_b = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
        semantic_embedding_threads=4,
    )

    provider_a = create_embedding_provider(config_a)
    provider_b = create_embedding_provider(config_b)

    assert provider_a is not provider_b


def test_embedding_provider_factory_forwards_openai_request_concurrency():
    """Factory should forward provider request concurrency for API-backed batching."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="openai",
        semantic_embedding_request_concurrency=6,
    )

    provider = create_embedding_provider(config)
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.request_concurrency == 6


def test_embedding_provider_factory_reset_clears_cache():
    """Cache reset helper should force provider recreation for the same config."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        semantic_search_enabled=True,
        semantic_embedding_provider="fastembed",
    )

    provider_first = create_embedding_provider(config)
    reset_embedding_provider_cache()
    provider_second = create_embedding_provider(config)

    assert provider_first is not provider_second
