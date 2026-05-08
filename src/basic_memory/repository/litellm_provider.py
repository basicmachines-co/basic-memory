"""LiteLLM-based embedding provider for semantic indexing.

Routes embedding requests to 100+ providers (OpenAI, Anthropic, Google, Azure,
Bedrock, Cohere, etc.) via the litellm SDK. No proxy server needed.

Model strings use the ``provider/model`` format, e.g.
``openai/text-embedding-3-small``, ``cohere/embed-english-v3.0``,
``azure/my-embedding-deployment``.

See https://docs.litellm.ai/docs/embedding/supported_embedding for all
supported embedding models.
"""

from __future__ import annotations

import asyncio
from typing import Any

from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


class LiteLLMEmbeddingProvider(EmbeddingProvider):
    """Embedding provider backed by the litellm SDK."""

    def __init__(
        self,
        model_name: str = "openai/text-embedding-3-small",
        *,
        batch_size: int = 64,
        request_concurrency: int = 4,
        dimensions: int = 1536,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.request_concurrency = request_concurrency
        self._api_key = api_key
        self._timeout = timeout

    def runtime_log_attrs(self) -> dict[str, int]:
        """Return provider-specific runtime settings suitable for startup logs."""
        return {
            "provider_batch_size": self.batch_size,
            "request_concurrency": self.request_concurrency,
        }

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            import litellm
        except ImportError as exc:
            raise SemanticDependenciesMissingError(
                "litellm dependency is missing. Install with: pip install litellm"
            ) from exc

        batches = [
            texts[start : start + self.batch_size]
            for start in range(0, len(texts), self.batch_size)
        ]
        batch_vectors: list[list[list[float]] | None] = [None] * len(batches)
        semaphore = asyncio.Semaphore(self.request_concurrency)

        async def embed_batch(batch_index: int, batch: list[str]) -> None:
            async with semaphore:
                params: dict[str, Any] = {
                    "model": self.model_name,
                    "input": batch,
                    "drop_params": True,
                    "timeout": self._timeout,
                }
                if self._api_key:
                    params["api_key"] = self._api_key

                response = await litellm.aembedding(**params)

            vectors_by_index: dict[int, list[float]] = {}
            for item in response.data:
                response_index = int(item["index"])
                vectors_by_index[response_index] = [float(v) for v in item["embedding"]]

            ordered_vectors: list[list[float]] = []
            for index in range(len(batch)):
                vector = vectors_by_index.get(index)
                if vector is None:
                    raise RuntimeError(
                        "LiteLLM embedding response is missing expected vector index."
                    )
                ordered_vectors.append(vector)

            batch_vectors[batch_index] = ordered_vectors

        await asyncio.gather(
            *(embed_batch(batch_index, batch) for batch_index, batch in enumerate(batches))
        )

        all_vectors: list[list[float]] = []
        for vectors in batch_vectors:
            if vectors is None:
                raise RuntimeError("LiteLLM embedding batch did not produce vectors.")
            all_vectors.extend(vectors)

        if all_vectors and len(all_vectors[0]) != self.dimensions:
            raise RuntimeError(
                f"Embedding model returned {len(all_vectors[0])}-dimensional vectors "
                f"but provider was configured for {self.dimensions} dimensions."
            )
        return all_vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0] if vectors else [0.0] * self.dimensions
