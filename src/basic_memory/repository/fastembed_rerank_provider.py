"""FastEmbed-based local cross-encoder reranker provider."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING, Any

from loguru import logger

from basic_memory.repository.rerank_provider import RerankProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError

if TYPE_CHECKING:
    from fastembed.rerank.cross_encoder import TextCrossEncoder  # pragma: no cover


class FastEmbedRerankProvider(RerankProvider):
    """Local ONNX cross-encoder reranker backed by FastEmbed.

    Shares the FastEmbed model cache with the embedding provider — reranker
    models live under a distinct model subdir, so the two never collide.
    """

    def __init__(
        self,
        model_name: str = "Xenova/ms-marco-MiniLM-L-6-v2",
        *,
        cache_dir: str | None = None,
        threads: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.threads = threads
        self._model: TextCrossEncoder | None = None
        # Serialize the one-time model load; concurrent first queries must not each
        # construct (and download) the ONNX model.
        self._model_lock = asyncio.Lock()

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {"model_name": self.model_name, "threads": self.threads}

    def _create_model(self) -> "TextCrossEncoder":
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatched tests
            raise SemanticDependenciesMissingError(
                "fastembed package is missing. "
                "Install/update basic-memory to include semantic dependencies: "
                "pip install -U basic-memory"
            ) from exc
        model_kwargs: dict[str, Any] = {"model_name": self.model_name}
        if self.cache_dir is not None:
            model_kwargs["cache_dir"] = self.cache_dir
        if self.threads is not None:
            model_kwargs["threads"] = self.threads
        return TextCrossEncoder(**model_kwargs)

    async def _load_model(self) -> "TextCrossEncoder":
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._create_model)
                logger.info("FastEmbed reranker loaded: model_name={model}", model=self.model_name)
            return self._model

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        model = await self._load_model()
        # TextCrossEncoder.rerank is sync/CPU-bound and yields one raw logit per doc
        # in input order; run it off the event loop and squash to [0, 1] so callers
        # get a bounded relevance on the same scale as API-based rerankers.
        return await asyncio.to_thread(
            lambda: [_sigmoid(float(score)) for score in model.rerank(query, documents)]
        )


def _sigmoid(x: float) -> float:
    """Map a cross-encoder logit to a [0, 1] relevance, clamping to avoid overflow."""
    # exp(710+) overflows a float; clamp well before that — the tails are ~0/~1 anyway.
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))
