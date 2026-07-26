"""FastEmbed-based local cross-encoder reranker provider."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING, Any

from loguru import logger
from requests import exceptions as requests_exceptions

from basic_memory.repository.rerank_provider import RerankProvider, validate_rerank_scores
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    RerankTransientError,
    SemanticDependenciesMissingError,
)

if TYPE_CHECKING:
    from fastembed.rerank.cross_encoder import TextCrossEncoder  # pragma: no cover


_TRANSIENT_DOWNLOAD_STATUS_CODES = frozenset({408, 425, 429})


def _is_transient_model_load_error(
    exc: requests_exceptions.RequestException | ValueError,
    model_name: str,
) -> bool:
    """Classify recoverable first-download failures without hiding model/config errors."""
    if isinstance(exc, (requests_exceptions.ConnectionError, requests_exceptions.Timeout)):
        return True
    if isinstance(exc, requests_exceptions.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else None
        return status_code in _TRANSIENT_DOWNLOAD_STATUS_CODES or (
            status_code is not None and status_code >= 500
        )
    # FastEmbed retries Hugging Face/GCS downloads internally and, after exhausting
    # both sources, replaces the transport cause with this model-specific ValueError.
    return str(exc) == f"Could not load model {model_name} from any source."


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
                try:
                    self._model = await asyncio.to_thread(self._create_model)
                except (requests_exceptions.RequestException, ValueError) as exc:
                    if not _is_transient_model_load_error(exc, self.model_name):
                        raise
                    raise RerankTransientError(
                        f"FastEmbed reranker model download failed temporarily: {self.model_name}"
                    ) from exc
                logger.info("FastEmbed reranker loaded: model_name={model}", model=self.model_name)
            return self._model

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        model = await self._load_model()
        # TextCrossEncoder.rerank is sync/CPU-bound and yields one raw logit per doc
        # in input order; run it off the event loop and squash to [0, 1] so callers
        # get a bounded relevance on the same scale as API-based rerankers.
        scores = await asyncio.to_thread(
            lambda: [_sigmoid(float(score)) for score in model.rerank(query, documents)]
        )
        return validate_rerank_scores(scores, len(documents))


def _sigmoid(x: float) -> float:
    """Map a cross-encoder logit to a [0, 1] relevance, clamping to avoid overflow."""
    if not math.isfinite(x):
        raise RerankProviderContractError(f"Reranker returned a non-finite logit: {x!r}")
    # exp(710+) overflows a float; clamp well before that — the tails are ~0/~1 anyway.
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))
