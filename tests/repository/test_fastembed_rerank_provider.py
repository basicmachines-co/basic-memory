"""Tests for FastEmbedRerankProvider."""

import builtins
import sys

import pytest

from basic_memory.repository.fastembed_rerank_provider import FastEmbedRerankProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


class _StubCrossEncoder:
    init_count = 0
    last_init_kwargs: dict = {}

    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None):
        _StubCrossEncoder.last_init_kwargs = {
            "model_name": model_name,
            "cache_dir": cache_dir,
            "threads": threads,
        }
        _StubCrossEncoder.init_count += 1

    def rerank(self, query: str, documents: list[str]):
        # Score = count of query-token overlaps, so tests can assert ordering.
        tokens = set(query.lower().split())
        for doc in documents:
            yield float(len(tokens & set(doc.lower().split())))


def _install_stub(monkeypatch) -> None:
    module = type(sys)("fastembed.rerank.cross_encoder")
    setattr(module, "TextCrossEncoder", _StubCrossEncoder)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)
    _StubCrossEncoder.init_count = 0


@pytest.mark.asyncio
async def test_lazy_loads_once_and_reuses_model(monkeypatch):
    _install_stub(monkeypatch)
    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    assert provider._model is None

    await provider.rerank("auth token", ["auth token doc", "unrelated"])
    await provider.rerank("auth token", ["another auth doc"])

    assert _StubCrossEncoder.init_count == 1
    assert provider._model is not None


@pytest.mark.asyncio
async def test_rerank_squashes_logits_to_unit_interval_in_input_order(monkeypatch):
    """Raw cross-encoder logits are sigmoid-squashed to [0, 1], preserving order."""
    _install_stub(monkeypatch)
    provider = FastEmbedRerankProvider(model_name="stub-reranker")

    # Stub logits: 0.0 (no overlap) and 2.0 (two-token overlap).
    scores = await provider.rerank("auth token", ["nothing here", "auth token match"])

    assert scores == [pytest.approx(0.5), pytest.approx(0.8807970779778823)]
    assert all(0.0 <= s <= 1.0 for s in scores)


@pytest.mark.asyncio
async def test_rerank_sigmoid_handles_extreme_logits(monkeypatch):
    """Large-magnitude logits are clamped so exp() never overflows."""
    module = type(sys)("fastembed.rerank.cross_encoder")

    class _Extreme:
        def __init__(self, **kwargs):
            pass

        def rerank(self, query, documents):
            yield -1000.0
            yield 1000.0

    setattr(module, "TextCrossEncoder", _Extreme)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)

    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    scores = await provider.rerank("q", ["a", "b"])
    assert scores[0] == pytest.approx(0.0, abs=1e-6)
    assert scores[1] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_empty_documents_short_circuit(monkeypatch):
    _install_stub(monkeypatch)
    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    assert await provider.rerank("auth", []) == []
    # Empty input must not trigger a model load.
    assert provider._model is None


@pytest.mark.asyncio
async def test_passes_cache_dir_and_threads_to_model(monkeypatch):
    _install_stub(monkeypatch)
    provider = FastEmbedRerankProvider(
        model_name="stub-reranker", cache_dir="/tmp/rr-cache", threads=3
    )
    await provider.rerank("auth", ["auth doc"])
    assert _StubCrossEncoder.last_init_kwargs == {
        "model_name": "stub-reranker",
        "cache_dir": "/tmp/rr-cache",
        "threads": 3,
    }


@pytest.mark.asyncio
async def test_missing_dependency_raises_actionable_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "fastembed.rerank.cross_encoder", raising=False)
    real_import = builtins.__import__

    def _raising_import(name, *args, **kwargs):
        if name.startswith("fastembed"):
            raise ImportError("no fastembed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raising_import)
    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    with pytest.raises(SemanticDependenciesMissingError):
        await provider.rerank("auth", ["auth doc"])


def test_runtime_log_attrs():
    provider = FastEmbedRerankProvider(model_name="stub-reranker", threads=2)
    assert provider.runtime_log_attrs() == {"model_name": "stub-reranker", "threads": 2}
