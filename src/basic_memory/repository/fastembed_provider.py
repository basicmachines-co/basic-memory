"""FastEmbed-based local embedding provider."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError

if TYPE_CHECKING:
    from fastembed import TextEmbedding  # type: ignore[import-not-found]  # pragma: no cover


class FastEmbedEmbeddingProvider(EmbeddingProvider):
    """Local ONNX embedding provider backed by FastEmbed."""

    _MODEL_ALIASES = {
        "bge-small-en-v1.5": "BAAI/bge-small-en-v1.5",
    }

    def _effective_parallel(self) -> int | None:
        return self.parallel if self.parallel is not None and self.parallel > 1 else None

    def __init__(
        self,
        model_name: str = "bge-small-en-v1.5",
        *,
        batch_size: int = 64,
        dimensions: int = 384,
        cache_dir: str | None = None,
        threads: int | None = None,
        parallel: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.cache_dir = cache_dir
        self.threads = threads
        self.parallel = parallel
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
                if self.cache_dir is not None and self.threads is not None:
                    return TextEmbedding(
                        model_name=resolved_model_name,
                        cache_dir=self.cache_dir,
                        threads=self.threads,
                    )
                if self.cache_dir is not None:
                    return TextEmbedding(model_name=resolved_model_name, cache_dir=self.cache_dir)
                if self.threads is not None:
                    return TextEmbedding(model_name=resolved_model_name, threads=self.threads)
                return TextEmbedding(model_name=resolved_model_name)

            self._model = await asyncio.to_thread(_create_model)
            logger.info(
                "FastEmbed model loaded: model_name={model_name} batch_size={batch_size} "
                "threads={threads} configured_parallel={configured_parallel} "
                "effective_parallel={effective_parallel}",
                model_name=self._MODEL_ALIASES.get(self.model_name, self.model_name),
                batch_size=self.batch_size,
                threads=self.threads,
                configured_parallel=self.parallel,
                effective_parallel=self._effective_parallel(),
            )
            return self._model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        model = await self._load_model()
        effective_parallel = self._effective_parallel()
        logger.debug(
            "FastEmbed embed_documents call: text_count={text_count} batch_size={batch_size} "
            "threads={threads} configured_parallel={configured_parallel} "
            "effective_parallel={effective_parallel}",
            text_count=len(texts),
            batch_size=self.batch_size,
            threads=self.threads,
            configured_parallel=self.parallel,
            effective_parallel=effective_parallel,
        )

        def _embed_batch() -> list[list[float]]:
            embed_kwargs: dict[str, int] = {"batch_size": self.batch_size}
            if effective_parallel is not None:
                embed_kwargs["parallel"] = effective_parallel
            vectors = list(model.embed(texts, **embed_kwargs))
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
