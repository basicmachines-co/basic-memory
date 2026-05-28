"""Opt-in live LiteLLM provider checks against real embedding APIs.

These tests intentionally do not run in normal CI. Enable them with
``BASIC_MEMORY_RUN_LITELLM_INTEGRATION=1`` and provider API keys when validating
new LiteLLM model support before merging or releasing.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any

import pytest

from basic_memory.repository.litellm_provider import LiteLLMEmbeddingProvider


pytestmark = [
    pytest.mark.semantic,
    pytest.mark.slow,
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("BASIC_MEMORY_RUN_LITELLM_INTEGRATION") != "1",
        reason="Set BASIC_MEMORY_RUN_LITELLM_INTEGRATION=1 to run live LiteLLM tests",
    ),
]


@dataclass(frozen=True)
class LiteLLMLiveCase:
    """A real LiteLLM embedding model to exercise end-to-end."""

    name: str
    model: str
    dimensions: int
    api_key_env: str | None = None
    document_input_type: str | None = None
    query_input_type: str | None = None


def _custom_cases() -> list[LiteLLMLiveCase]:
    """Load additional live model cases from BASIC_MEMORY_TEST_LITELLM_CASES."""
    raw = os.getenv("BASIC_MEMORY_TEST_LITELLM_CASES")
    if not raw:
        return []

    values = json.loads(raw)
    if not isinstance(values, list):
        raise ValueError("BASIC_MEMORY_TEST_LITELLM_CASES must be a JSON array")

    cases: list[LiteLLMLiveCase] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Each LiteLLM live case must be a JSON object")
        case_data: dict[str, Any] = value
        cases.append(
            LiteLLMLiveCase(
                name=str(case_data["name"]),
                model=str(case_data["model"]),
                dimensions=int(case_data["dimensions"]),
                api_key_env=case_data.get("api_key_env"),
                document_input_type=case_data.get("document_input_type"),
                query_input_type=case_data.get("query_input_type"),
            )
        )
    return cases


def _live_cases() -> list[LiteLLMLiveCase | Any]:
    """Return built-in and user-supplied live cases whose credentials are available."""
    cases: list[LiteLLMLiveCase] = []

    if os.getenv("OPENAI_API_KEY"):
        cases.append(
            LiteLLMLiveCase(
                name="openai-text-embedding-3-small",
                model="openai/text-embedding-3-small",
                dimensions=1536,
                api_key_env="OPENAI_API_KEY",
            )
        )

    if os.getenv("COHERE_API_KEY"):
        cases.append(
            LiteLLMLiveCase(
                name="cohere-embed-english-v3",
                model="cohere/embed-english-v3.0",
                dimensions=1024,
                api_key_env="COHERE_API_KEY",
            )
        )

    cases.extend(_custom_cases())
    if cases:
        return cases

    return [
        pytest.param(
            None,
            marks=pytest.mark.skip(
                reason=(
                    "No LiteLLM live cases configured. Set OPENAI_API_KEY, "
                    "COHERE_API_KEY, or BASIC_MEMORY_TEST_LITELLM_CASES."
                )
            ),
        )
    ]


def _cosine(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity for live ranking sanity checks."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _assert_valid_vector(vector: list[float], dimensions: int) -> None:
    """Assert provider output is a usable normalized vector."""
    assert len(vector) == dimensions
    assert all(math.isfinite(value) for value in vector)
    norm = math.sqrt(sum(value * value for value in vector))
    assert norm == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    _live_cases(),
    ids=lambda case: case.name if isinstance(case, LiteLLMLiveCase) else "no-live-cases",
)
async def test_litellm_live_model_embeds_documents_and_queries(
    case: LiteLLMLiveCase,
) -> None:
    """A live LiteLLM model should embed documents and rank a related query higher."""
    api_key = os.getenv(case.api_key_env) if case.api_key_env else None
    provider = LiteLLMEmbeddingProvider(
        model_name=case.model,
        dimensions=case.dimensions,
        batch_size=2,
        api_key=api_key,
        timeout=60.0,
        document_input_type=case.document_input_type,
        query_input_type=case.query_input_type,
    )

    documents = [
        "OAuth login refresh tokens keep an authenticated web session active.",
        "A sourdough starter ferments flour and water before bread baking.",
    ]
    vectors = await provider.embed_documents(documents)
    query_vector = await provider.embed_query("authentication login token flow")

    assert len(vectors) == 2
    for vector in [*vectors, query_vector]:
        _assert_valid_vector(vector, case.dimensions)

    assert _cosine(query_vector, vectors[0]) > _cosine(query_vector, vectors[1])
