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
import math
import os
from typing import Any

from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


def _default_input_types(model_name: str) -> tuple[str | None, str | None]:
    """Return role-specific LiteLLM input_type defaults for known asymmetric models."""
    normalized = model_name.strip().lower()

    # Cohere v3 embeddings require search_document/search_query to distinguish
    # index-time passages from retrieval-time queries. LiteLLM supports both
    # direct Cohere model names and provider-prefixed forms.
    cohere_v3 = (
        normalized.startswith("cohere/")
        or normalized.startswith("bedrock/cohere.")
        or normalized.startswith("cohere.")
        or normalized.startswith("embed-")
    ) and "-v3" in normalized
    if cohere_v3:
        return "search_document", "search_query"

    # NVIDIA retrieval embeddings use passage/query roles. The provider prefix
    # is part of LiteLLM's model routing, so this stays narrowly scoped.
    if normalized.startswith("nvidia_nim/"):
        return "passage", "query"

    return None, None


def _import_litellm() -> Any:
    """Import LiteLLM without letting its import-time dotenv hook read cwd secrets."""
    # Constraint: LiteLLM 1.85.0 loads .env files at import time when
    # LITELLM_MODE defaults to DEV. Basic Memory intentionally does not load
    # arbitrary cwd .env files, so set the production mode before importing
    # unless the caller already made an explicit LiteLLM choice.
    os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

    try:
        import litellm
    except ImportError as exc:
        raise SemanticDependenciesMissingError(
            "litellm dependency is missing. Install with: pip install litellm"
        ) from exc

    return litellm


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
        document_input_type: str | None = None,
        query_input_type: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.request_concurrency = request_concurrency
        self._api_key = api_key
        self._timeout = timeout
        default_document_input_type, default_query_input_type = _default_input_types(model_name)
        self.document_input_type = document_input_type or default_document_input_type
        self.query_input_type = query_input_type or default_query_input_type

    def runtime_log_attrs(self) -> dict[str, Any]:
        """Return provider-specific runtime settings suitable for startup logs."""
        attrs: dict[str, Any] = {
            "provider_batch_size": self.batch_size,
            "request_concurrency": self.request_concurrency,
        }
        if self.document_input_type:
            attrs["document_input_type"] = self.document_input_type
        if self.query_input_type:
            attrs["query_input_type"] = self.query_input_type
        return attrs

    async def _embed(self, texts: list[str], *, input_type: str | None) -> list[list[float]]:
        if not texts:
            return []

        litellm = _import_litellm()

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
                if input_type:
                    params["input_type"] = input_type

                response = await litellm.aembedding(**params)

            vectors_by_index: dict[int, list[float]] = {}
            for item in response.data:
                response_index = int(item.index)
                if response_index in vectors_by_index:
                    raise RuntimeError(
                        "LiteLLM embedding response returned duplicate vector indexes."
                    )
                vectors_by_index[response_index] = [float(v) for v in item.embedding]

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

        # sqlite_search_repository.py maps L2 distance to cosine similarity via
        # `1 - L²/2`, which is correct only for unit-normalized vectors. LiteLLM
        # routes to many backends (Cohere, Vertex, Bedrock, etc.); not all of
        # them return normalized embeddings, so we normalize here to honor the
        # provider contract regardless of the underlying model.
        normalized: list[list[float]] = []
        for vector in all_vectors:
            norm = math.sqrt(sum(x * x for x in vector))
            if norm > 0:
                normalized.append([x / norm for x in vector])
            else:
                normalized.append(vector)

        if normalized and len(normalized[0]) != self.dimensions:
            raise RuntimeError(
                f"Embedding model returned {len(normalized[0])}-dimensional vectors "
                f"but provider was configured for {self.dimensions} dimensions."
            )
        return normalized

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, input_type=self.document_input_type)

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], input_type=self.query_input_type)
        return vectors[0] if vectors else [0.0] * self.dimensions
