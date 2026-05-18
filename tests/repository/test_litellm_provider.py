"""Tests for LiteLLMEmbeddingProvider and factory litellm branch."""

import asyncio
import builtins
import sys
from types import SimpleNamespace

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import (
    create_embedding_provider,
    reset_embedding_provider_cache,
)
from basic_memory.repository.litellm_provider import LiteLLMEmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


def _make_embedding_response(inputs: list[str], dim: int = 3):
    """Build a fake litellm.aembedding response matching the real shape."""
    data = []
    for index, text in enumerate(inputs):
        base = float(len(text))
        data.append({"index": index, "embedding": [base + float(d) for d in range(dim)]})
    return SimpleNamespace(data=data)


def _install_litellm_stub(monkeypatch, dim: int = 3):
    """Install a fake litellm module and return the mock aembedding callable."""
    calls: list[dict] = []

    async def _aembedding(**kwargs):
        calls.append(kwargs)
        return _make_embedding_response(kwargs["input"], dim)

    module = type(sys)("litellm")
    setattr(module, "aembedding", _aembedding)
    monkeypatch.setitem(sys.modules, "litellm", module)
    return calls


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_embedding_provider_cache()
    yield
    reset_embedding_provider_cache()


@pytest.mark.asyncio
async def test_litellm_provider_embed_query(monkeypatch):
    """embed_query should return a single vector through litellm.aembedding."""
    _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(
        model_name="openai/text-embedding-3-small", batch_size=2, dimensions=3
    )
    result = await provider.embed_query("hello world")
    assert len(result) == 3
    assert all(isinstance(v, float) for v in result)


@pytest.mark.asyncio
async def test_litellm_provider_embed_documents(monkeypatch):
    """embed_documents should return vectors for each input text."""
    _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(
        model_name="openai/text-embedding-3-small", batch_size=2, dimensions=3
    )
    texts = ["first doc", "second doc", "third doc"]
    result = await provider.embed_documents(texts)
    assert len(result) == 3
    assert all(len(v) == 3 for v in result)


@pytest.mark.asyncio
async def test_litellm_provider_empty_input(monkeypatch):
    """embed_documents with empty list should return empty list."""
    _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(dimensions=3)
    result = await provider.embed_documents([])
    assert result == []


@pytest.mark.asyncio
async def test_litellm_provider_batching(monkeypatch):
    """Provider should split inputs into batches of batch_size."""
    calls = _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(
        model_name="openai/text-embedding-3-small", batch_size=2, dimensions=3
    )
    texts = ["a", "b", "c", "d", "e"]
    result = await provider.embed_documents(texts)

    assert len(result) == 5
    assert len(calls) == 3  # 2 + 2 + 1


@pytest.mark.asyncio
async def test_litellm_provider_api_key_forwarded(monkeypatch):
    """api_key should be passed to litellm.aembedding when set."""
    calls = _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(
        model_name="openai/text-embedding-3-small",
        api_key="sk-test-key",
        dimensions=3,
    )
    await provider.embed_query("test")
    assert calls[0]["api_key"] == "sk-test-key"


@pytest.mark.asyncio
async def test_litellm_provider_api_key_omitted_when_none(monkeypatch):
    """api_key should not appear in kwargs when not set."""
    calls = _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(
        model_name="openai/text-embedding-3-small", dimensions=3
    )
    await provider.embed_query("test")
    assert "api_key" not in calls[0]


@pytest.mark.asyncio
async def test_litellm_provider_drop_params_always_set(monkeypatch):
    """drop_params=True should always be in the call kwargs."""
    calls = _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(dimensions=3)
    await provider.embed_query("test")
    assert calls[0]["drop_params"] is True


@pytest.mark.asyncio
async def test_litellm_provider_dimension_mismatch_raises_error(monkeypatch):
    """Provider should fail fast when response dimensions differ from configured."""
    _install_litellm_stub(monkeypatch, dim=3)
    provider = LiteLLMEmbeddingProvider(dimensions=5)
    with pytest.raises(RuntimeError, match="3-dimensional vectors"):
        await provider.embed_documents(["test text"])


@pytest.mark.asyncio
async def test_litellm_provider_missing_dependency_raises_actionable_error(monkeypatch):
    """Missing litellm package should raise SemanticDependenciesMissingError."""
    monkeypatch.delitem(sys.modules, "litellm", raising=False)
    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "litellm":
            raise ImportError("litellm not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    provider = LiteLLMEmbeddingProvider(model_name="openai/text-embedding-3-small")
    with pytest.raises(SemanticDependenciesMissingError):
        await provider.embed_query("test")


@pytest.mark.asyncio
async def test_litellm_provider_output_ordering(monkeypatch):
    """Vectors should be returned in the same order as input texts."""
    _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(dimensions=3, batch_size=2)
    texts = ["short", "a longer text here"]
    result = await provider.embed_documents(texts)

    assert result[0][0] == float(len("short"))
    assert result[1][0] == float(len("a longer text here"))


def test_factory_selects_litellm_provider():
    """Factory should select LiteLLMEmbeddingProvider for litellm config."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test": "/tmp/basic-memory-test"},
        default_project="test",
        semantic_search_enabled=True,
        semantic_embedding_provider="litellm",
        semantic_embedding_model="openai/text-embedding-3-small",
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, LiteLLMEmbeddingProvider)
    assert provider.model_name == "openai/text-embedding-3-small"


def test_factory_maps_default_model_for_litellm():
    """Factory should remap bge-small-en-v1.5 default to openai/text-embedding-3-small."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test": "/tmp/basic-memory-test"},
        default_project="test",
        semantic_search_enabled=True,
        semantic_embedding_provider="litellm",
        semantic_embedding_model="bge-small-en-v1.5",
    )
    provider = create_embedding_provider(config)
    assert isinstance(provider, LiteLLMEmbeddingProvider)
    assert provider.model_name == "openai/text-embedding-3-small"


def test_runtime_log_attrs():
    """runtime_log_attrs should return batch_size and concurrency."""
    provider = LiteLLMEmbeddingProvider(batch_size=32, request_concurrency=8)
    attrs = provider.runtime_log_attrs()
    assert attrs["provider_batch_size"] == 32
    assert attrs["request_concurrency"] == 8
