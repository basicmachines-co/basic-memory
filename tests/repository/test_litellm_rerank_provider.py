"""Tests for LiteLLMRerankProvider."""

import types

import pytest

from basic_memory.repository.litellm_rerank_provider import LiteLLMRerankProvider
from basic_memory.repository.semantic_errors import RerankProviderContractError


class _RerankResult:
    """Object-style rerank result item (Cohere/Jina SDK shape)."""

    def __init__(self, index: int, relevance_score: float):
        self.index = index
        self.relevance_score = relevance_score


class _Response:
    def __init__(self, results):
        self.results = results


def _fake_litellm(response, recorder: dict) -> types.SimpleNamespace:
    async def arerank(**params):
        recorder.update(params)
        return response

    return types.SimpleNamespace(arerank=arerank)


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
async def test_rerank_handles_object_style_results_and_routing(monkeypatch):
    recorder: dict = {}
    response = _Response([_RerankResult(0, 0.7), _RerankResult(1, 0.2)])
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
