"""Tests for FastEmbedEmbeddingProvider."""

import builtins
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

    def embed(self, texts: list[str], batch_size: int = 64, parallel: int | None = None):
        self.embed_calls += 1
        _StubTextEmbedding.last_embed_kwargs = {
            "batch_size": batch_size,
            "parallel": parallel,
        }
        for text in texts:
            if "wide" in text:
                yield _StubVector([1.0, 0.0, 0.0, 0.0, 0.5])
            else:
                yield _StubVector([1.0, 0.0, 0.0, 0.0])


@pytest.mark.asyncio
async def test_fastembed_provider_lazy_loads_and_reuses_model(monkeypatch):
    """Provider should instantiate FastEmbed lazily and reuse the loaded model."""
    module = type(sys)("fastembed")
    module.TextEmbedding = _StubTextEmbedding
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
    module.TextEmbedding = _StubTextEmbedding
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
    module.TextEmbedding = _StubTextEmbedding
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
