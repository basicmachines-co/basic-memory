"""Tests for LiteLLMRerankProvider."""

import types

import pytest
from pydantic import BaseModel, ValidationError

from basic_memory.repository.litellm_rerank_provider import LiteLLMRerankProvider
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    RerankTransientError,
)


class _Response:
    def __init__(self, results):
        self.results = results


class _TransientProviderError(RuntimeError):
    pass


class _BadGatewayError(RuntimeError):
    pass


class _SDKRerankResponse(BaseModel):
    results: list[dict]


def _fake_litellm(response, recorder: dict, *, exc: Exception | None = None):
    async def arerank(**params):
        recorder.update(params)
        if exc is not None:
            raise exc
        return response

    return types.SimpleNamespace(
        arerank=arerank,
        Timeout=_TransientProviderError,
        APIConnectionError=_TransientProviderError,
        RateLimitError=_TransientProviderError,
        BadGatewayError=_BadGatewayError,
        ServiceUnavailableError=_TransientProviderError,
        InternalServerError=_TransientProviderError,
    )


@pytest.mark.asyncio
async def test_rerank_realigns_out_of_order_indexed_results(monkeypatch):
    """Rerank responses are indexed and may arrive out of order; realign to input."""
    recorder: dict = {}
    response = _Response(
        [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.1},
            {"index": 1, "relevance_score": 0.5},
        ]
    )
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(response, recorder),
    )
    provider = LiteLLMRerankProvider(model_name="cohere/rerank-v3.5")

    scores = await provider.rerank("q", ["doc0", "doc1", "doc2"])

    assert scores == [0.1, 0.5, 0.9]
    assert recorder["model"] == "cohere/rerank-v3.5"
    assert recorder["top_n"] == 3


@pytest.mark.asyncio
async def test_rerank_forwards_routing_params(monkeypatch):
    recorder: dict = {}
    response = _Response(
        [{"index": 0, "relevance_score": 0.7}, {"index": 1, "relevance_score": 0.2}]
    )
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(response, recorder),
    )
    provider = LiteLLMRerankProvider(
        model_name="jina_ai/jina-reranker-v2",
        api_key="secret",
        api_base="https://rr.example",
    )

    scores = await provider.rerank("q", ["a", "b"])

    assert scores == [0.7, 0.2]
    assert recorder["api_key"] == "secret"
    assert recorder["api_base"] == "https://rr.example"


@pytest.mark.asyncio
async def test_incomplete_response_raises(monkeypatch):
    """A response that omits a requested document is a fault, not a silent 0.0."""
    response = _Response([{"index": 1, "relevance_score": 0.8}])  # index 0 missing
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(response, {}),
    )
    provider = LiteLLMRerankProvider()
    with pytest.raises(RerankProviderContractError, match="covered 1 of 2 documents"):
        await provider.rerank("q", ["dropped", "kept"])


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_index", [-1, 2])
async def test_out_of_range_index_raises(monkeypatch, bad_index):
    """Indices outside [0, len) are a malformed response, not a valid position.

    A negative index would silently overwrite a wrong slot via Python indexing and
    could even satisfy the coverage check; fail fast instead.
    """
    response = _Response(
        [
            {"index": bad_index, "relevance_score": 0.8},
            {"index": 0, "relevance_score": 0.4},
        ]
    )
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(response, {}),
    )
    provider = LiteLLMRerankProvider()
    with pytest.raises(RerankProviderContractError, match="out of range"):
        await provider.rerank("q", ["a", "b"])


@pytest.mark.asyncio
async def test_duplicate_index_raises(monkeypatch):
    """A repeated index with otherwise-full coverage slips past the coverage check.

    `[0, 1, 0]` for two documents leaves `seen == {0, 1}` — the count check passes —
    while the second index-0 item silently overwrites the first score. Reject the
    duplicate so the "each index exactly once" contract holds.
    """
    response = _Response(
        [
            {"index": 0, "relevance_score": 0.8},
            {"index": 1, "relevance_score": 0.5},
            {"index": 0, "relevance_score": 0.1},
        ]
    )
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(response, {}),
    )
    provider = LiteLLMRerankProvider()
    with pytest.raises(RerankProviderContractError, match="repeated index 0"):
        await provider.rerank("q", ["a", "b"])


@pytest.mark.asyncio
@pytest.mark.parametrize("results", [None, []])
async def test_missing_results_raises(monkeypatch, results):
    """litellm's RerankResponse.results is optional; None/empty is a contract break."""
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(_Response(results), {}),
    )
    provider = LiteLLMRerankProvider()
    with pytest.raises(RerankProviderContractError, match="no results"):
        await provider.rerank("q", ["a", "b"])


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_score", [-0.1, 1.1, float("nan"), float("inf")])
async def test_invalid_relevance_score_raises(monkeypatch, bad_score):
    response = _Response([{"index": 0, "relevance_score": bad_score}])
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(response, {}),
    )
    provider = LiteLLMRerankProvider()

    with pytest.raises(RerankProviderContractError, match=r"finite and in \[0, 1\]"):
        await provider.rerank("q", ["a"])


@pytest.mark.asyncio
async def test_transient_provider_error_is_classified_for_fallback(monkeypatch):
    transient_error = _TransientProviderError("provider unavailable")
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(None, {}, exc=transient_error),
    )
    provider = LiteLLMRerankProvider()

    with pytest.raises(RerankTransientError) as caught:
        await provider.rerank("q", ["a"])

    assert caught.value.__cause__ is transient_error


@pytest.mark.asyncio
async def test_bad_gateway_error_is_classified_for_fallback(monkeypatch):
    bad_gateway_error = _BadGatewayError("upstream returned 502")
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(None, {}, exc=bad_gateway_error),
    )
    provider = LiteLLMRerankProvider()

    with pytest.raises(RerankTransientError) as caught:
        await provider.rerank("q", ["a"])

    assert caught.value.__cause__ is bad_gateway_error


@pytest.mark.asyncio
async def test_sdk_response_validation_error_is_provider_contract_error(monkeypatch):
    with pytest.raises(ValidationError) as invalid_response:
        _SDKRerankResponse.model_validate({})
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(None, {}, exc=invalid_response.value),
    )
    provider = LiteLLMRerankProvider()

    with pytest.raises(RerankProviderContractError, match="invalid response") as caught:
        await provider.rerank("q", ["a"])

    assert caught.value.__cause__ is invalid_response.value


@pytest.mark.asyncio
async def test_unknown_provider_error_surfaces(monkeypatch):
    permanent_error = RuntimeError("invalid credentials or model")
    monkeypatch.setattr(
        "basic_memory.repository.litellm_rerank_provider._import_litellm",
        lambda: _fake_litellm(None, {}, exc=permanent_error),
    )
    provider = LiteLLMRerankProvider()

    with pytest.raises(RuntimeError, match="invalid credentials or model"):
        await provider.rerank("q", ["a"])


@pytest.mark.asyncio
async def test_empty_documents_short_circuit(monkeypatch):
    called = False

    def _boom():
        nonlocal called
        called = True
        raise AssertionError("litellm must not be imported for empty input")

    monkeypatch.setattr("basic_memory.repository.litellm_rerank_provider._import_litellm", _boom)
    provider = LiteLLMRerankProvider()
    assert await provider.rerank("q", []) == []
    assert called is False


def test_runtime_log_attrs():
    provider = LiteLLMRerankProvider(model_name="cohere/rerank-v3.5", api_base="https://rr")
    assert provider.runtime_log_attrs() == {
        "model_name": "cohere/rerank-v3.5",
        "api_base_set": True,
    }
