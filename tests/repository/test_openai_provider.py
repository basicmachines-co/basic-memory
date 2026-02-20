"""Tests for OpenAIEmbeddingProvider and embedding provider factory."""

import builtins
import sys
from types import SimpleNamespace

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
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


@pytest.mark.asyncio
async def test_openai_provider_lazy_loads_and_reuses_client(monkeypatch):
    """Provider should instantiate AsyncOpenAI lazily and reuse a single client."""
    module = type(sys)("openai")
    module.AsyncOpenAI = _StubAsyncOpenAI
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
    module.AsyncOpenAI = _StubAsyncOpenAI
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
    module.AsyncOpenAI = _StubAsyncOpenAI
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
