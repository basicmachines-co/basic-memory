"""Tests for OrcaRouterEmbeddingProvider and its embedding provider factory branch."""

import builtins
import sys
from types import SimpleNamespace

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import (
    create_embedding_provider,
    reset_embedding_provider_cache,
)
from basic_memory.repository.orcarouter_provider import (
    ORCAROUTER_DEFAULT_BASE_URL,
    ORCAROUTER_DEFAULT_MODEL,
    OrcaRouterEmbeddingProvider,
)
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


@pytest.fixture(autouse=True)
def _reset_embedding_provider_cache_fixture():
    reset_embedding_provider_cache()
    yield
    reset_embedding_provider_cache()


def _install_stub_openai(monkeypatch) -> None:
    module = type(sys)("openai")
    setattr(module, "AsyncOpenAI", _StubAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", module)


def _make_config(**overrides) -> BasicMemoryConfig:
    defaults = {
        "env": "test",
        "projects": {"test-project": "/tmp/basic-memory-test"},
        "default_project": "test-project",
        "semantic_search_enabled": True,
    }
    defaults.update(overrides)
    return BasicMemoryConfig(**defaults)


# --- Provider behavior --------------------------------------------------------


@pytest.mark.asyncio
async def test_orcarouter_provider_lazy_loads_and_reuses_client(monkeypatch):
    """Provider should instantiate AsyncOpenAI lazily, use OrcaRouter base URL, and reuse a single client."""
    _install_stub_openai(monkeypatch)
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    _StubAsyncOpenAI.init_count = 0

    provider = OrcaRouterEmbeddingProvider(
        model_name=ORCAROUTER_DEFAULT_MODEL, batch_size=2, dimensions=3
    )
    assert provider._client is None

    first = await provider.embed_query("auth query")
    second = await provider.embed_documents(["queue task", "relation sync"])

    assert _StubAsyncOpenAI.init_count == 1
    assert provider._client is not None
    client = provider._client
    assert client.base_url == ORCAROUTER_DEFAULT_BASE_URL
    assert client.api_key == "sk-orca-test"
    assert len(first) == 3
    assert len(second) == 2
    assert len(second[0]) == 3


@pytest.mark.asyncio
async def test_orcarouter_provider_respects_explicit_api_key_and_base_url(monkeypatch):
    """Explicit api_key/base_url should win over env/defaults."""
    _install_stub_openai(monkeypatch)
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-env")
    _StubAsyncOpenAI.init_count = 0

    provider = OrcaRouterEmbeddingProvider(
        model_name=ORCAROUTER_DEFAULT_MODEL,
        api_key="sk-orca-explicit",
        base_url="https://custom.example/v1",
        dimensions=3,
    )
    await provider.embed_query("test")

    assert provider._client is not None
    client = provider._client
    assert client.api_key == "sk-orca-explicit"
    assert client.base_url == "https://custom.example/v1"


@pytest.mark.asyncio
async def test_orcarouter_provider_dimension_mismatch_raises_error(monkeypatch):
    """Provider should fail fast when response dimensions differ from configured dimensions."""
    _install_stub_openai(monkeypatch)
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")

    provider = OrcaRouterEmbeddingProvider(dimensions=2)
    with pytest.raises(RuntimeError, match="3-dimensional vectors"):
        await provider.embed_documents(["semantic note"])


@pytest.mark.asyncio
async def test_orcarouter_provider_missing_dependency_raises_actionable_error(monkeypatch):
    """Missing openai package should raise SemanticDependenciesMissingError."""
    monkeypatch.delitem(sys.modules, "openai", raising=False)
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai":
            raise ImportError("openai not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    provider = OrcaRouterEmbeddingProvider(model_name=ORCAROUTER_DEFAULT_MODEL)
    with pytest.raises(SemanticDependenciesMissingError) as error:
        await provider.embed_query("test")

    assert "pip install -U basic-memory" in str(error.value)


@pytest.mark.asyncio
async def test_orcarouter_provider_missing_api_key_raises_error(monkeypatch):
    """ORCAROUTER_API_KEY is required unless api_key is passed explicitly."""
    _install_stub_openai(monkeypatch)
    monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)

    provider = OrcaRouterEmbeddingProvider(model_name=ORCAROUTER_DEFAULT_MODEL)
    with pytest.raises(SemanticDependenciesMissingError) as error:
        await provider.embed_query("test")

    assert "ORCAROUTER_API_KEY" in str(error.value)


# --- Factory selection --------------------------------------------------------


def test_embedding_provider_factory_selects_orcarouter_and_applies_default_model():
    """Factory should map local default model to OrcaRouter default when provider is orcarouter."""
    config = _make_config(
        semantic_embedding_provider="orcarouter",
        semantic_embedding_model="bge-small-en-v1.5",
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, OrcaRouterEmbeddingProvider)
    assert provider.model_name == ORCAROUTER_DEFAULT_MODEL
    assert provider._base_url == ORCAROUTER_DEFAULT_BASE_URL


def test_embedding_provider_factory_orcarouter_uses_default_dimensions():
    """Factory should use OrcaRouter default 1536 dimensions when unset."""
    config = _make_config(semantic_embedding_provider="orcarouter")
    provider = create_embedding_provider(config)
    assert isinstance(provider, OrcaRouterEmbeddingProvider)
    assert provider.dimensions == 1536


def test_embedding_provider_factory_passes_custom_dimensions_to_orcarouter():
    """Factory should forward semantic_embedding_dimensions to the OrcaRouter provider."""
    config = _make_config(
        semantic_embedding_provider="orcarouter",
        semantic_embedding_dimensions=3072,
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, OrcaRouterEmbeddingProvider)
    assert provider.dimensions == 3072


def test_embedding_provider_factory_orcarouter_forwards_request_concurrency():
    """Factory should forward provider request concurrency for API-backed batching."""
    config = _make_config(
        semantic_embedding_provider="orcarouter",
        semantic_embedding_request_concurrency=6,
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, OrcaRouterEmbeddingProvider)
    assert provider.request_concurrency == 6


def test_embedding_provider_identity_orcarouter():
    """configured_embedding_provider_identity should name OrcaRouterEmbeddingProvider."""
    from basic_memory.repository.embedding_provider_factory import (
        configured_embedding_provider_identity,
    )

    config = _make_config(
        semantic_embedding_provider="orcarouter",
        semantic_embedding_model="openai/text-embedding-3-small",
    )
    identity = configured_embedding_provider_identity(config)
    assert identity == "OrcaRouterEmbeddingProvider:openai/text-embedding-3-small:1536"
