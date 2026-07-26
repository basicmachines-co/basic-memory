"""Tests for FastEmbedRerankProvider."""

import asyncio
import builtins
import sys
import threading

import pytest
from requests import Response, exceptions as requests_exceptions

from basic_memory.repository.fastembed_rerank_provider import FastEmbedRerankProvider
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    RerankTransientError,
    SemanticDependenciesMissingError,
)


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


def _http_error(status_code: int) -> requests_exceptions.HTTPError:
    response = Response()
    response.status_code = status_code
    return requests_exceptions.HTTPError(f"HTTP {status_code}", response=response)


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
async def test_cancelled_first_waiter_does_not_start_a_second_model_load(monkeypatch):
    """Later searches join the worker thread left running by a cancelled request."""
    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    loop = asyncio.get_running_loop()
    load_started = asyncio.Event()
    release_load = threading.Event()
    model = object()
    load_count = 0

    def create_model():
        nonlocal load_count
        load_count += 1
        loop.call_soon_threadsafe(load_started.set)
        assert release_load.wait(timeout=5)
        return model

    monkeypatch.setattr(provider, "_create_model", create_model)

    first_waiter = asyncio.create_task(provider._load_model())
    await load_started.wait()
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    second_waiter = asyncio.create_task(provider._load_model())
    for _ in range(100):
        if load_count > 1:
            break
        await asyncio.sleep(0.001)
    release_load.set()

    assert await second_waiter is model
    assert load_count == 1


@pytest.mark.asyncio
async def test_waiter_reuses_model_loaded_before_it_acquires_the_lock(monkeypatch):
    """The in-lock double check wins if another load finishes while a caller waits."""
    _install_stub(monkeypatch)
    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    model = provider._create_model()
    await provider._model_lock.acquire()
    waiter = asyncio.create_task(provider._load_model())
    await asyncio.sleep(0)

    provider._model = model
    provider._model_lock.release()

    assert await waiter is model


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
@pytest.mark.parametrize("logit", [float("nan"), float("inf"), float("-inf")])
async def test_rerank_rejects_non_finite_logits(monkeypatch, logit):
    module = type(sys)("fastembed.rerank.cross_encoder")

    class _NonFinite:
        def __init__(self, **kwargs):
            pass

        def rerank(self, query, documents):
            yield logit

    setattr(module, "TextCrossEncoder", _NonFinite)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)

    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    with pytest.raises(RerankProviderContractError, match="non-finite logit"):
        await provider.rerank("q", ["a"])


@pytest.mark.asyncio
async def test_rerank_rejects_missing_model_scores(monkeypatch):
    module = type(sys)("fastembed.rerank.cross_encoder")

    class _Incomplete:
        def __init__(self, **kwargs):
            pass

        def rerank(self, query, documents):
            yield 0.5

    setattr(module, "TextCrossEncoder", _Incomplete)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)

    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    with pytest.raises(RerankProviderContractError, match="1 scores for 2 documents"):
        await provider.rerank("q", ["a", "b"])


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "load_error",
    [
        requests_exceptions.Timeout("download timed out"),
        requests_exceptions.ConnectionError("connection reset"),
        _http_error(503),
        ValueError("Could not load model stub-reranker from any source."),
    ],
)
async def test_transient_model_download_error_is_classified(monkeypatch, load_error):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    module = type(sys)("fastembed.rerank.cross_encoder")

    class _DownloadFailure:
        def __init__(self, **kwargs):
            raise load_error

    setattr(module, "TextCrossEncoder", _DownloadFailure)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)

    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    with pytest.raises(RerankTransientError, match="model download failed temporarily"):
        await provider.rerank("auth", ["auth doc"])
    assert provider._model is None


@pytest.mark.asyncio
@pytest.mark.parametrize("offline_env", ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"])
async def test_offline_cache_miss_remains_permanent(monkeypatch, offline_env):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setenv(offline_env, "1")
    module = type(sys)("fastembed.rerank.cross_encoder")
    load_error = ValueError("Could not load model stub-reranker from any source.")

    class _OfflineCacheMiss:
        def __init__(self, **kwargs):
            raise load_error

    setattr(module, "TextCrossEncoder", _OfflineCacheMiss)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)

    provider = FastEmbedRerankProvider(model_name="stub-reranker")
    with pytest.raises(ValueError, match="Could not load model"):
        await provider.rerank("auth", ["auth doc"])
    assert provider._model is None


@pytest.mark.asyncio
async def test_unsupported_model_error_remains_permanent(monkeypatch):
    module = type(sys)("fastembed.rerank.cross_encoder")

    class _UnsupportedModel:
        def __init__(self, **kwargs):
            raise ValueError("Model unknown is not supported in TextCrossEncoder.")

    setattr(module, "TextCrossEncoder", _UnsupportedModel)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)

    provider = FastEmbedRerankProvider(model_name="unknown")
    with pytest.raises(ValueError, match="not supported"):
        await provider.rerank("auth", ["auth doc"])


@pytest.mark.asyncio
async def test_download_auth_error_remains_permanent(monkeypatch):
    module = type(sys)("fastembed.rerank.cross_encoder")
    auth_error = _http_error(401)

    class _Unauthorized:
        def __init__(self, **kwargs):
            raise auth_error

    setattr(module, "TextCrossEncoder", _Unauthorized)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)

    provider = FastEmbedRerankProvider(model_name="private-model")
    with pytest.raises(requests_exceptions.HTTPError):
        await provider.rerank("auth", ["auth doc"])


def test_runtime_log_attrs():
    provider = FastEmbedRerankProvider(model_name="stub-reranker", threads=2)
    assert provider.runtime_log_attrs() == {"model_name": "stub-reranker", "threads": 2}
