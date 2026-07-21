"""LiteLLM-based reranker provider.

Routes rerank requests to any provider LiteLLM supports (Cohere, Jina, Voyage,
Together, AWS Bedrock, self-hosted OpenAI-compatible endpoints, ...) via
``litellm.arerank``. Model strings use the ``provider/model`` format, e.g.
``cohere/rerank-v3.5`` or ``jina_ai/jina-reranker-v2-base-multilingual``.

See https://docs.litellm.ai/docs/rerank for supported rerank models.
"""

from __future__ import annotations

from typing import Any

from basic_memory.repository.litellm_provider import _import_litellm
from basic_memory.repository.rerank_provider import RerankProvider
from basic_memory.repository.semantic_errors import RerankProviderContractError


class LiteLLMRerankProvider(RerankProvider):
    """Reranker provider backed by the litellm SDK."""

    def __init__(
        self,
        model_name: str = "cohere/rerank-v3.5",
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.model_name = model_name
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {"model_name": self.model_name, "api_base_set": self._api_base is not None}

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []

        litellm = _import_litellm()
        params: dict[str, Any] = {
            "model": self.model_name,
            "query": query,
            "documents": documents,
            # Ask for every candidate back so we can rescore the full pool; the
            # caller decides the final cut.
            "top_n": len(documents),
            "timeout": self._timeout,
        }
        if self._api_key:
            params["api_key"] = self._api_key
        if self._api_base is not None:
            params["api_base"] = self._api_base

        response = await litellm.arerank(**params)
        results = response.results if hasattr(response, "results") else response["results"]

        # Rerank responses are indexed and may arrive out of order, so rebuild an
        # input-aligned score vector. We request top_n == len(documents), so a
        # complete response must cover every index; a gap means a truncated/partial
        # response we must not silently paper over with a 0.0 floor (fail fast).
        scores = [0.0] * len(documents)
        seen: set[int] = set()
        for item in results:
            if isinstance(item, dict):
                index = int(item["index"])
                score = float(item["relevance_score"])
            else:
                index = int(item.index)
                score = float(item.relevance_score)
            scores[index] = score
            seen.add(index)
        if len(seen) != len(documents):
            raise RerankProviderContractError(
                f"Rerank response covered {len(seen)} of {len(documents)} documents."
            )
        return scores
