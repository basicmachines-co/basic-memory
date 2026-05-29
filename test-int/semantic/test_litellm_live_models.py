"""Opt-in live LiteLLM provider checks against real embedding APIs."""

from __future__ import annotations

import os
from typing import Any

import pytest

from semantic.litellm_live_harness import LiteLLMLiveCase, configured_cases, evaluate_case


pytestmark = [
    pytest.mark.semantic,
    pytest.mark.slow,
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("BASIC_MEMORY_RUN_LITELLM_INTEGRATION") != "1",
        reason="Set BASIC_MEMORY_RUN_LITELLM_INTEGRATION=1 to run live LiteLLM tests",
    ),
]


def _live_cases() -> list[LiteLLMLiveCase | Any]:
    """Return built-in and user-supplied live cases whose credentials are available."""
    cases = configured_cases(os.environ)
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
    result = await evaluate_case(case)

    assert result.related_score > result.distractor_score
