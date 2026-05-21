"""Tests for FastEmbedEmbeddingProvider."""

import builtins
import math
import sys

import pytest

from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


class _StubVector:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return self._values


class _StubTextEmbedding:
    init_count = 0
    last_init_kwargs: dict = {}
    last_embed_kwargs: dict = {}

    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None):
        self.model_name = model_name
        self.embed_calls = 0
        _StubTextEmbedding.last_init_kwargs = {
            "model_name": model_name,
            "cache_dir": cache_dir,
            "threads": threads,
        }
        _StubTextEmbedding.init_count += 1

    def embed(self, texts: list[str], batch_size: int = 64, **kwargs):
        self.embed_calls += 1
        _StubTextEmbedding.last_embed_kwargs = {"batch_size": batch_size, **kwargs}
        for text in texts:
            if "wide" in text:
                yield _StubVector([1.0, 0.0, 0.0, 0.0, 0.5])
            else:
                yield _StubVector([1.0, 0.0, 0.0, 0.0])


@pytest.mark.asyncio
async def test_fastembed_provider_lazy_loads_and_reuses_model(monkeypatch):
    """Provider should instantiate FastEmbed lazily and reuse the loaded model."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _StubTextEmbedding.init_count = 0

    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4)
    assert provider._model is None

    first = await provider.embed_query("auth query")
    second = await provider.embed_documents(["database query"])

    assert _StubTextEmbedding.init_count == 1
    assert provider._model is not None
    assert len(first) == 4
    assert len(second) == 1
    assert len(second[0]) == 4


@pytest.mark.asyncio
async def test_fastembed_provider_dimension_mismatch_raises_error(monkeypatch):
    """Provider should fail fast when model output dimensions differ from configured dimensions."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)

    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4)
    with pytest.raises(RuntimeError, match="5-dimensional vectors"):
        await provider.embed_documents(["wide vector"])


@pytest.mark.asyncio
async def test_fastembed_provider_missing_dependency_raises_actionable_error(monkeypatch):
    """Missing fastembed package should raise SemanticDependenciesMissingError."""
    monkeypatch.delitem(sys.modules, "fastembed", raising=False)
    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fastembed":
            raise ImportError("fastembed not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    provider = FastEmbedEmbeddingProvider(model_name="stub-model")
    with pytest.raises(SemanticDependenciesMissingError) as error:
        await provider.embed_query("test")

    assert "pip install -U basic-memory" in str(error.value)


@pytest.mark.asyncio
async def test_fastembed_provider_passes_runtime_knobs_to_fastembed(monkeypatch):
    """Provider should pass optional runtime tuning knobs through to FastEmbed."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _StubTextEmbedding.last_init_kwargs = {}
    _StubTextEmbedding.last_embed_kwargs = {}

    provider = FastEmbedEmbeddingProvider(
        model_name="stub-model",
        dimensions=4,
        batch_size=8,
        cache_dir="/tmp/fastembed-cache",
        threads=3,
        parallel=2,
    )
    await provider.embed_documents(["runtime knobs"])

    assert _StubTextEmbedding.last_init_kwargs == {
        "model_name": "stub-model",
        "cache_dir": "/tmp/fastembed-cache",
        "threads": 3,
    }
    assert _StubTextEmbedding.last_embed_kwargs == {"batch_size": 8, "parallel": 2}


@pytest.mark.asyncio
async def test_fastembed_provider_parallel_one_disables_multiprocessing(monkeypatch):
    """parallel=1 should not pass FastEmbed multiprocessing kwargs."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _StubTextEmbedding.last_embed_kwargs = {}

    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4, parallel=1)
    await provider.embed_documents(["parallel guardrail"])

    assert _StubTextEmbedding.last_embed_kwargs == {"batch_size": 64}


@pytest.mark.asyncio
async def test_fastembed_provider_parallel_two_passes_multiprocessing(monkeypatch):
    """parallel>1 should keep passing FastEmbed multiprocessing kwargs."""
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _StubTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _StubTextEmbedding.last_embed_kwargs = {}

    provider = FastEmbedEmbeddingProvider(model_name="stub-model", dimensions=4, parallel=2)
    await provider.embed_documents(["parallel enabled"])

    assert _StubTextEmbedding.last_embed_kwargs == {"batch_size": 64, "parallel": 2}


class _UnormalizedVector:
    """Stub vector with norm != 1 (simulates multilingual models like paraphrase-multilingual-*)."""

    def __init__(self, values):
        self._values = values

    def tolist(self):
        return self._values


class _UnnormalizedTextEmbedding:
    def __init__(self, model_name: str, **_kwargs):
        self.model_name = model_name

    def embed(self, texts: list[str], **_kwargs):
        # Return a vector with norm ~= 2.9 (typical for multilingual MiniLM models)
        for _ in texts:
            yield _UnormalizedVector([1.5, 2.0, 1.0, 0.5])


@pytest.mark.asyncio
async def test_fastembed_provider_l2_normalizes_output_vectors(monkeypatch):
    """Returned vectors must be unit-normalized regardless of the raw model output.

    sqlite_search_repository uses a formula that assumes norm == 1. Models such as
    paraphrase-multilingual-MiniLM-L12-v2 return vectors with norm ~2.9, which breaks
    cosine similarity scoring. The provider must apply L2 normalization before returning.
    """
    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _UnnormalizedTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)

    provider = FastEmbedEmbeddingProvider(model_name="stub-multilingual", dimensions=4)
    result = await provider.embed_documents(["some text"])

    assert len(result) == 1
    norm = math.sqrt(sum(x * x for x in result[0]))
    assert abs(norm - 1.0) < 1e-6, f"Expected unit norm, got {norm}"


@pytest.mark.asyncio
async def test_fastembed_provider_zero_vector_does_not_raise(monkeypatch):
    """A zero vector from the model must be returned as-is without a division error."""

    class _ZeroEmbedding:
        def __init__(self, model_name: str, **_kwargs):
            pass

        def embed(self, texts: list[str], **_kwargs):
            for _ in texts:
                yield _UnormalizedVector([0.0, 0.0, 0.0, 0.0])

    module = type(sys)("fastembed")
    setattr(module, "TextEmbedding", _ZeroEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)

    provider = FastEmbedEmbeddingProvider(model_name="stub-zero", dimensions=4)
    result = await provider.embed_documents(["zero vector"])

    assert result == [[0.0, 0.0, 0.0, 0.0]]
