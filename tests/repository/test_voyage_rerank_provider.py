"""Failure modes of the Voyage reranker provider, its config validation and factory wiring."""

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.rerank_provider_factory import (
    create_rerank_provider,
    reset_rerank_provider_cache,
)
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    RerankTransientError,
    SemanticDependenciesMissingError,
)
from basic_memory.repository.voyage_rerank_provider import VoyageRerankProvider
from tests.repository.voyage_fakes import ERRORS, install_fake_voyageai, rerank_response


@pytest.fixture(autouse=True)
def _voyage_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    reset_rerank_provider_cache()
    yield
    reset_rerank_provider_cache()


def _config(**overrides) -> BasicMemoryConfig:
    return BasicMemoryConfig(
        env="test", reranker_enabled=True, reranker_provider="voyage", **overrides
    )


# --- Explicit model selection ---


@pytest.mark.parametrize(
    "model", ["jinaai/jina-reranker-v1-tiny-en", "cohere/rerank-v3.5", "rerank-2", "voyage-4"]
)
def test_config_rejects_models_that_are_not_voyage_rerankers(model: str) -> None:
    with pytest.raises(ValidationError, match="requires reranker_model to name a Voyage rerank"):
        _config(reranker_model=model)


def test_default_model_is_not_silently_replaced() -> None:
    with pytest.raises(ValidationError, match="Supported models: rerank-3,"):
        _config()


def test_provider_rejects_unknown_model_when_built_directly() -> None:
    with pytest.raises(ValueError, match="requires reranker_model"):
        VoyageRerankProvider("rerank-2")


def test_candidate_pool_is_bounded_by_the_request_document_limit() -> None:
    with pytest.raises(ValidationError, match="exceeds the 1000 documents"):
        _config(reranker_model="rerank-3", reranker_candidates=1001)

    assert _config(reranker_model="rerank-3", reranker_candidates=1000).reranker_candidates == 1000


def test_custom_api_base_is_rejected() -> None:
    with pytest.raises(ValidationError, match="reranker_api_base"):
        _config(reranker_model="rerank-3", reranker_api_base="http://localhost:1234")


def test_factory_builds_a_cached_voyage_provider() -> None:
    config = _config(reranker_model="rerank-3-lite")

    provider = create_rerank_provider(config)

    assert isinstance(provider, VoyageRerankProvider)
    assert provider.model_name == "rerank-3-lite"
    assert provider.runtime_log_attrs() == {"model_name": "rerank-3-lite"}
    assert create_rerank_provider(config) is provider


# --- Request and response ---


@pytest.mark.asyncio
async def test_scores_are_realigned_to_input_order(monkeypatch) -> None:
    client = install_fake_voyageai(
        monkeypatch, rerank_response((2, 0.9), (0, 0.5), (1, 0.1)), rerank_response((0, 0.3))
    )
    provider = VoyageRerankProvider("rerank-3")

    scores = await provider.rerank("q", ["d0", "d1", "d2"])
    await provider.rerank("q", ["only"])

    assert scores == [0.5, 0.1, 0.9]
    (endpoint, args, kwargs), _ = client.instances[0].calls
    assert (endpoint, args, kwargs) == ("rerank", ("q", ["d0", "d1", "d2"]), {"model": "rerank-3"})
    # The client is reused across calls and never retries inside the SDK.
    assert len(client.instances) == 1
    assert client.instances[0].init_kwargs["max_retries"] == 0
    assert await provider.rerank("q", []) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("results", "message"),
    [
        ([(0, 0.5), (0, 0.4)], "repeated index 0"),
        ([(0, 0.5), (5, 0.4)], "index 5 is out of range"),
        ([(0, 0.5)], "covered 1 of 2"),
        ([(0, 0.5), (1, 1.5)], "must be finite and in"),
    ],
)
async def test_malformed_responses_are_contract_errors(monkeypatch, results, message) -> None:
    install_fake_voyageai(monkeypatch, rerank_response(*results))

    with pytest.raises(RerankProviderContractError, match=message):
        await VoyageRerankProvider("rerank-3").rerank("q", ["a", "b"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_name",
    [
        "Timeout",
        "APIConnectionError",
        "RateLimitError",
        "ServerError",
        "ServiceUnavailableError",
        "TryAgain",
    ],
)
async def test_transient_voyage_errors_are_retryable(monkeypatch, error_name) -> None:
    install_fake_voyageai(monkeypatch, getattr(ERRORS, error_name)("busy"))

    with pytest.raises(RerankTransientError, match="rerank-3.*busy"):
        await VoyageRerankProvider("rerank-3").rerank("q", ["a"])


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["AuthenticationError", "InvalidRequestError"])
async def test_permanent_voyage_errors_report_the_detail_field(monkeypatch, error_name) -> None:
    install_fake_voyageai(
        monkeypatch, getattr(ERRORS, error_name)("sdk", json_body={"detail": "Invalid API key."})
    )

    with pytest.raises(SemanticDependenciesMissingError, match="Invalid API key."):
        await VoyageRerankProvider("rerank-3").rerank("q", ["a"])


@pytest.mark.asyncio
async def test_missing_api_key_fails(monkeypatch) -> None:
    install_fake_voyageai(monkeypatch)
    monkeypatch.delenv("VOYAGE_API_KEY")

    with pytest.raises(SemanticDependenciesMissingError, match="reranker_api_key"):
        await VoyageRerankProvider("rerank-3").rerank("q", ["a"])


@pytest.mark.asyncio
async def test_unclassified_api_error_is_an_upstream_contract_error(monkeypatch) -> None:
    install_fake_voyageai(monkeypatch, ERRORS.APIError("<html>bad gateway</html>"))

    with pytest.raises(RerankProviderContractError, match="unrecognized error.*bad gateway"):
        await VoyageRerankProvider("rerank-3").rerank("q", ["a"])
