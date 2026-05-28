"""Tests for LiteLLMEmbeddingProvider and factory litellm branch."""

import builtins
import math
import os
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
        data.append(
            SimpleNamespace(
                index=index,
                embedding=[base + float(d) for d in range(dim)],
            )
        )
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
    provider = LiteLLMEmbeddingProvider(model_name="openai/text-embedding-3-small", dimensions=3)
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
async def test_litellm_provider_uses_cohere_document_and_query_input_types(monkeypatch):
    """Cohere v3 embeddings require different input_type values per embedding role."""
    calls = _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(
        model_name="cohere/embed-english-v3.0",
        batch_size=2,
        dimensions=3,
    )

    await provider.embed_documents(["indexed passage"])
    await provider.embed_query("retrieval query")

    assert calls[0]["input_type"] == "search_document"
    assert calls[1]["input_type"] == "search_query"


@pytest.mark.asyncio
async def test_litellm_provider_uses_explicit_document_and_query_input_types(monkeypatch):
    """Explicit input_type overrides should support asymmetric providers beyond Cohere."""
    calls = _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(
        model_name="nvidia_nim/nvidia/embed-qa-4",
        batch_size=2,
        dimensions=3,
        document_input_type="passage",
        query_input_type="query",
    )

    await provider.embed_documents(["indexed passage"])
    await provider.embed_query("retrieval query")

    assert calls[0]["input_type"] == "passage"
    assert calls[1]["input_type"] == "query"


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
async def test_litellm_provider_sets_production_mode_before_import(monkeypatch):
    """Unset LiteLLM mode should not let LiteLLM import load cwd .env files."""
    monkeypatch.delitem(sys.modules, "litellm", raising=False)
    monkeypatch.delenv("LITELLM_MODE", raising=False)
    observed_modes: list[str | None] = []
    original_import = builtins.__import__

    async def _aembedding(**kwargs):
        return _make_embedding_response(kwargs["input"])

    def _observing_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "litellm":
            observed_modes.append(os.environ.get("LITELLM_MODE"))
            module = type(sys)("litellm")
            setattr(module, "aembedding", _aembedding)
            monkeypatch.setitem(sys.modules, "litellm", module)
            return module
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _observing_import)

    provider = LiteLLMEmbeddingProvider(dimensions=3)
    await provider.embed_query("test")

    assert observed_modes == ["PRODUCTION"]
    assert os.environ["LITELLM_MODE"] == "PRODUCTION"


@pytest.mark.asyncio
async def test_litellm_provider_preserves_explicit_litellm_mode(monkeypatch):
    """An explicit LiteLLM mode should stay under the caller's control."""
    monkeypatch.setenv("LITELLM_MODE", "CUSTOM")
    _install_litellm_stub(monkeypatch)

    provider = LiteLLMEmbeddingProvider(dimensions=3)
    await provider.embed_query("test")

    assert os.environ["LITELLM_MODE"] == "CUSTOM"


@pytest.mark.asyncio
async def test_litellm_provider_output_ordering(monkeypatch):
    """Vectors should be returned in the same order as input texts.

    The mock builds vectors as ``[len(text), len(text)+1, len(text)+2]`` per
    input, then the provider L2-normalizes them. Reconstruct the expected
    normalized vectors and assert positional match — this catches both
    ordering regressions and normalization regressions in one go.
    """
    _install_litellm_stub(monkeypatch)
    provider = LiteLLMEmbeddingProvider(dimensions=3, batch_size=2)
    texts = ["short", "a longer text here"]
    result = await provider.embed_documents(texts)

    def _expected(text: str) -> list[float]:
        base = float(len(text))
        raw = [base + float(d) for d in range(3)]
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]

    assert result[0] == pytest.approx(_expected("short"))
    assert result[1] == pytest.approx(_expected("a longer text here"))


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


def test_factory_forwards_litellm_document_and_query_input_types():
    """Factory should pass role-specific LiteLLM input_type config to the provider."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test": "/tmp/basic-memory-test"},
        default_project="test",
        semantic_search_enabled=True,
        semantic_embedding_provider="litellm",
        semantic_embedding_model="nvidia_nim/nvidia/embed-qa-4",
        semantic_embedding_dimensions=1024,
        semantic_embedding_document_input_type="passage",
        semantic_embedding_query_input_type="query",
    )
    provider = create_embedding_provider(config)

    assert isinstance(provider, LiteLLMEmbeddingProvider)
    assert provider.dimensions == 1024
    assert provider.document_input_type == "passage"
    assert provider.query_input_type == "query"


def test_factory_requires_litellm_dimensions_for_custom_models():
    """Custom LiteLLM models need explicit dimensions before vector tables are created."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test": "/tmp/basic-memory-test"},
        default_project="test",
        semantic_search_enabled=True,
        semantic_embedding_provider="litellm",
        semantic_embedding_model="cohere/embed-english-v3.0",
    )

    with pytest.raises(ValueError, match="semantic_embedding_dimensions"):
        create_embedding_provider(config)


def test_runtime_log_attrs():
    """runtime_log_attrs should return batch_size and concurrency."""
    provider = LiteLLMEmbeddingProvider(batch_size=32, request_concurrency=8)
    attrs = provider.runtime_log_attrs()
    assert attrs["provider_batch_size"] == 32
    assert attrs["request_concurrency"] == 8


@pytest.mark.asyncio
async def test_litellm_provider_l2_normalizes_output_vectors(monkeypatch):
    """Returned vectors must be unit-normalized regardless of backend output.

    sqlite_search_repository maps L2 distance to cosine similarity via
    ``1 - L²/2``, which is correct only for unit norm. Several backends
    routed through LiteLLM (Cohere, Vertex, Bedrock) do not return
    normalized vectors, so the provider must normalize at its boundary.
    """

    async def _aembedding(**kwargs):
        # Raw vector with norm ~3.74 — must be normalized to unit length.
        data = [
            SimpleNamespace(index=i, embedding=[1.0, 2.0, 3.0]) for i in range(len(kwargs["input"]))
        ]
        return SimpleNamespace(data=data)

    module = type(sys)("litellm")
    setattr(module, "aembedding", _aembedding)
    monkeypatch.setitem(sys.modules, "litellm", module)

    provider = LiteLLMEmbeddingProvider(dimensions=3)
    result = await provider.embed_documents(["some text"])

    assert len(result) == 1
    norm = math.sqrt(sum(x * x for x in result[0]))
    assert abs(norm - 1.0) < 1e-6, f"Expected unit norm, got {norm}"


@pytest.mark.asyncio
async def test_litellm_provider_zero_vector_does_not_raise(monkeypatch):
    """A zero vector from the backend must pass through without a division error."""

    async def _aembedding(**kwargs):
        data = [
            SimpleNamespace(index=i, embedding=[0.0, 0.0, 0.0]) for i in range(len(kwargs["input"]))
        ]
        return SimpleNamespace(data=data)

    module = type(sys)("litellm")
    setattr(module, "aembedding", _aembedding)
    monkeypatch.setitem(sys.modules, "litellm", module)

    provider = LiteLLMEmbeddingProvider(dimensions=3)
    result = await provider.embed_documents(["zero vector"])

    assert result == [[0.0, 0.0, 0.0]]


@pytest.mark.asyncio
async def test_litellm_provider_duplicate_index_raises_error(monkeypatch):
    """A backend returning duplicate indexes is malformed and must fail fast."""

    async def _aembedding(**kwargs):
        # Both items claim index 0 — ambiguous response.
        data = [
            SimpleNamespace(index=0, embedding=[1.0, 0.0, 0.0]),
            SimpleNamespace(index=0, embedding=[0.0, 1.0, 0.0]),
        ]
        return SimpleNamespace(data=data)

    module = type(sys)("litellm")
    setattr(module, "aembedding", _aembedding)
    monkeypatch.setitem(sys.modules, "litellm", module)

    provider = LiteLLMEmbeddingProvider(dimensions=3)
    with pytest.raises(RuntimeError, match="duplicate vector indexes"):
        await provider.embed_documents(["a", "b"])
