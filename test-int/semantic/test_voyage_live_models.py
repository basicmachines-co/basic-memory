"""Opt-in live Voyage AI checks: the factory-built providers against the real API."""

from __future__ import annotations

import os

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import (
    create_embedding_provider,
    reset_embedding_provider_cache,
)
from basic_memory.repository.rerank_provider_factory import (
    create_rerank_provider,
    reset_rerank_provider_cache,
)

pytestmark = [
    pytest.mark.semantic,
    pytest.mark.slow,
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("BASIC_MEMORY_RUN_VOYAGE_INTEGRATION") != "1" or not os.getenv("VOYAGE_API_KEY"),
        reason="Set BASIC_MEMORY_RUN_VOYAGE_INTEGRATION=1 and VOYAGE_API_KEY to run live Voyage tests",
    ),
]

RELATED = "Cats are small carnivorous mammals that purr and hunt mice."
DISTRACTOR = "Boil spaghetti in salted water for ten minutes."
QUERY = "feline pets that catch rodents"


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@pytest.fixture(autouse=True)
def _fresh_provider_caches():
    reset_embedding_provider_cache()
    reset_rerank_provider_cache()
    yield
    reset_embedding_provider_cache()
    reset_rerank_provider_cache()


# voyage-4: the plain embeddings endpoint. voyage-context-4: the contextualized endpoint
# with auto-chunking for documents. Dimension 256 also exercises the Matryoshka request.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "dimensions"),
    [("voyage-4", None), ("voyage-4", 256), ("voyage-context-4", None), ("voyage-context-4", 256)],
)
async def test_live_embedding_model_ranks_the_related_document_first(
    model: str, dimensions: int | None
) -> None:
    config = BasicMemoryConfig(
        env="test",
        semantic_embedding_provider="voyage",
        semantic_embedding_model=model,
        semantic_embedding_dimensions=dimensions,
    )
    provider = create_embedding_provider(config)

    related, distractor = await provider.embed_documents([RELATED, DISTRACTOR])
    query = await provider.embed_query(QUERY)

    assert len(query) == len(related) == len(distractor) == (dimensions or 1024)
    assert _cosine(query, related) > _cosine(query, distractor)


@pytest.mark.asyncio
async def test_live_rerank_model_scores_the_related_document_highest() -> None:
    config = BasicMemoryConfig(
        env="test",
        reranker_enabled=True,
        reranker_provider="voyage",
        reranker_model="rerank-3",
    )
    provider = create_rerank_provider(config)
    assert provider is not None

    scores = await provider.rerank(QUERY, [DISTRACTOR, RELATED])

    assert scores[1] > scores[0]
