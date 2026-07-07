"""Tests for the opt-in LiteLLM live evaluation harness."""

from __future__ import annotations

import json

import pytest

from semantic.litellm_live_harness import (
    LiteLLMLiveCase,
    configured_cases,
    evaluate_case,
    load_custom_cases,
)


class FakeProvider:
    """Minimal provider double for exercising live harness logic without network calls."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        assert len(texts) == 2
        return [[1.0, 0.0], [0.0, 1.0]]

    async def embed_query(self, text: str) -> list[float]:
        assert text
        return [1.0, 0.0]


class WrongRankingProvider(FakeProvider):
    """Provider double that ranks the distractor document higher."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        assert len(texts) == 2
        return [[0.0, 1.0], [1.0, 0.0]]


def test_load_custom_cases_accepts_endpoint_roles_and_dimension_forwarding():
    """Custom JSON should preserve endpoint, role, and dimensions options."""
    raw = json.dumps(
        [
            {
                "name": "azure-small-512",
                "model": "azure/basic-memory-embeddings",
                "dimensions": 512,
                "api_key_env": "AZURE_API_KEY",
                "api_base": "https://example.openai.azure.com",
                "document_input_type": "passage",
                "query_input_type": "query",
                "forward_dimensions": True,
            }
        ]
    )

    cases = load_custom_cases(raw)

    assert cases == [
        LiteLLMLiveCase(
            name="azure-small-512",
            model="azure/basic-memory-embeddings",
            dimensions=512,
            api_key_env="AZURE_API_KEY",
            api_base="https://example.openai.azure.com",
            document_input_type="passage",
            query_input_type="query",
            forward_dimensions=True,
        )
    ]


def test_configured_cases_include_available_built_ins():
    """Exported provider keys should enable the built-in OpenAI and Cohere live cases."""
    cases = configured_cases(
        {
            "OPENAI_API_KEY": "openai-key",
            "COHERE_API_KEY": "cohere-key",
        }
    )

    assert [case.name for case in cases] == [
        "openai-text-embedding-3-small",
        "cohere-embed-english-v3",
    ]


def test_configured_cases_respects_explicit_empty_environment():
    """Tests should not accidentally inherit live keys when an empty env is passed."""
    assert configured_cases({}) == []


@pytest.mark.asyncio
async def test_evaluate_case_reports_metrics_for_valid_provider():
    """A passing case should report dimensions, scores, norms, and latency."""
    result = await evaluate_case(
        LiteLLMLiveCase(
            name="fake-provider",
            model="fake/model",
            dimensions=2,
            api_key_env="FAKE_API_KEY",
        ),
        environ={"FAKE_API_KEY": "secret"},
        provider_factory=FakeProvider,
    )

    assert result.name == "fake-provider"
    assert result.dimensions == 2
    assert result.related_score == pytest.approx(1.0)
    assert result.distractor_score == pytest.approx(0.0)
    assert result.min_norm == pytest.approx(1.0)
    assert result.max_norm == pytest.approx(1.0)
    assert result.total_latency_ms >= 0


@pytest.mark.asyncio
async def test_evaluate_case_forwards_api_base_to_provider():
    """Custom live cases should exercise the configured endpoint."""
    provider_kwargs: dict[str, object] = {}

    def provider_factory(**kwargs):
        provider_kwargs.update(kwargs)
        return FakeProvider(**kwargs)

    await evaluate_case(
        LiteLLMLiveCase(
            name="local-provider",
            model="openai/local-embedding-model",
            dimensions=2,
            api_base="http://127.0.0.1:8080/v1",
        ),
        provider_factory=provider_factory,
    )

    assert provider_kwargs["api_base"] == "http://127.0.0.1:8080/v1"


@pytest.mark.asyncio
async def test_evaluate_case_rejects_wrong_ranking():
    """A case should fail when the query ranks the distractor document higher."""
    with pytest.raises(AssertionError, match="ranked the related document"):
        await evaluate_case(
            LiteLLMLiveCase(
                name="bad-provider",
                model="fake/model",
                dimensions=2,
            ),
            provider_factory=WrongRankingProvider,
        )
