"""FastEmbed-based local embedding provider."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError

if TYPE_CHECKING:
    from fastembed import TextEmbedding  # type: ignore[import-not-found]  # pragma: no cover


class FastEmbedEmbeddingProvider(EmbeddingProvider):
    """Local ONNX embedding provider backed by FastEmbed."""

    _MODEL_ALIASES = {
        "bge-small-en-v1.5": "BAAI/bge-small-en-v1.5",
    }

    def __init__(
        self,
        model_name: str = "bge-small-en-v1.5",
        *,
        batch_size: int = 64,
        dimensions: int = 384,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._model: TextEmbedding | None = None
        self._model_lock = asyncio.Lock()

    async def _load_model(self) -> "TextEmbedding":
        if self._model is not None:
            return self._model

        async with self._model_lock:
            if self._model is not None:
                return self._model

            def _create_model() -> "TextEmbedding":
                try:
                    from fastembed import TextEmbedding  # type: ignore[import-not-found]
                except (
                    ImportError
                ) as exc:  # pragma: no cover - exercised via tests with monkeypatch
                    raise SemanticDependenciesMissingError(
                        "fastembed package is missing. "
                        "Install/update basic-memory to include semantic dependencies: "
                        "pip install -U basic-memory"
                    ) from exc
                resolved_model_name = self._MODEL_ALIASES.get(self.model_name, self.model_name)
                return TextEmbedding(model_name=resolved_model_name)

            self._model = await asyncio.to_thread(_create_model)
            return self._model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        model = await self._load_model()

        def _embed_batch() -> list[list[float]]:
            vectors = list(model.embed(texts, batch_size=self.batch_size))
            normalized: list[list[float]] = []
            for vector in vectors:
                values = vector.tolist() if hasattr(vector, "tolist") else vector
                normalized.append([float(value) for value in values])
            return normalized

        vectors = await asyncio.to_thread(_embed_batch)
        if vectors and len(vectors[0]) != self.dimensions:
            raise RuntimeError(
                f"Embedding model returned {len(vectors[0])}-dimensional vectors "
                f"but provider was configured for {self.dimensions} dimensions."
            )
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0] if vectors else [0.0] * self.dimensions
