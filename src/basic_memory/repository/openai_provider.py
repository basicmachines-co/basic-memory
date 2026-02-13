"""OpenAI-based embedding provider for cloud or API-backed semantic indexing."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embedding provider backed by OpenAI's embeddings API."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        *,
        batch_size: int = 64,
        dimensions: int = 1536,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        async with self._client_lock:
            if self._client is not None:
                return self._client

            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - covered via monkeypatch tests
                raise SemanticDependenciesMissingError(
                    "OpenAI dependency is missing. Reinstall basic-memory: pip install basic-memory"
                ) from exc

            api_key = self._api_key or os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise SemanticDependenciesMissingError(
                    "OpenAI embedding provider requires OPENAI_API_KEY."
                )

            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=self._base_url,
                timeout=self._timeout,
            )
            return self._client

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = await self._get_client()
        all_vectors: list[list[float]] = []

        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = await client.embeddings.create(
                model=self.model_name,
                input=batch,
            )
            vectors_by_index: dict[int, list[float]] = {
                int(item.index): [float(value) for value in item.embedding]
                for item in response.data
            }
            for index in range(len(batch)):
                vector = vectors_by_index.get(index)
                if vector is None:
                    raise RuntimeError(
                        "OpenAI embedding response is missing expected vector index."
                    )
                all_vectors.append(vector)

        if all_vectors and len(all_vectors[0]) != self.dimensions:
            raise RuntimeError(
                f"Embedding model returned {len(all_vectors[0])}-dimensional vectors "
                f"but provider was configured for {self.dimensions} dimensions."
            )
        return all_vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0] if vectors else [0.0] * self.dimensions
