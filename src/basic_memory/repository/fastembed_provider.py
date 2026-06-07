"""FastEmbed-based local embedding provider."""

from __future__ import annotations

import asyncio
import math
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError

if TYPE_CHECKING:
    from fastembed import TextEmbedding  # pragma: no cover


# Substrings that identify a missing/corrupt on-disk model artifact (as opposed to a
# config error or a genuinely offline machine). An interrupted FastEmbed download leaves
# the HuggingFace snapshot dir present but missing ``model_optimized.onnx``; the ONNX
# runtime then raises ``NO_SUCHFILE`` and every subsequent load repeats it until the
# cache is cleared. Matched case-insensitively against the exception text.
_CORRUPT_MODEL_ERROR_MARKERS = (
    "no_suchfile",
    "model_optimized.onnx",
    "file doesn't exist",
    "no such file",
)


class FastEmbedEmbeddingProvider(EmbeddingProvider):
    """Local ONNX embedding provider backed by FastEmbed."""

    _MODEL_ALIASES = {
        "bge-small-en-v1.5": "BAAI/bge-small-en-v1.5",
    }

    def _effective_parallel(self) -> int | None:
        return self.parallel if self.parallel is not None and self.parallel > 1 else None

    def runtime_log_attrs(self) -> dict[str, int | str | None]:
        """Return the resolved runtime knobs that shape FastEmbed throughput."""
        return {
            "provider_batch_size": self.batch_size,
            "threads": self.threads,
            "configured_parallel": self.parallel,
            "effective_parallel": self._effective_parallel(),
        }

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

    def _create_model(self) -> "TextEmbedding":
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - exercised via tests with monkeypatch
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

    def _model_cache_subdirs(self) -> list[Path]:
        """Resolve the HuggingFace cache subdir(s) for this model under ``cache_dir``.

        FastEmbed stores each model under ``<cache_dir>/models--<org>--<repo>`` where the
        repo is the model's HuggingFace source (e.g. ``BAAI/bge-small-en-v1.5`` resolves to
        ``models--qdrant--bge-small-en-v1.5-onnx-q``). We resolve the source from FastEmbed's
        own model description so the deletion is scoped to exactly this model's tree — never
        the whole cache or unrelated models.
        """
        if self.cache_dir is None:
            return []

        resolved_model_name = self._MODEL_ALIASES.get(self.model_name, self.model_name)
        hf_sources: set[str] = set()
        try:
            from fastembed import TextEmbedding

            for description in TextEmbedding._list_supported_models():
                if description.model == resolved_model_name:
                    hf_source = description.sources.hf
                    if hf_source:
                        hf_sources.add(hf_source)
        except Exception as exc:  # pragma: no cover - defensive: never block self-heal on lookup
            logger.warning(
                "Could not resolve FastEmbed model source for cache cleanup: "
                "model_name={model_name} error={error}",
                model_name=resolved_model_name,
                error=exc,
            )

        cache_root = Path(self.cache_dir)
        # HuggingFace hub names cache dirs ``models--<repo with '/' -> '--'>``.
        return [cache_root / f"models--{source.replace('/', '--')}" for source in hf_sources]

    def _purge_corrupt_model_cache(self) -> bool:
        """Delete this model's on-disk cache subtree so the next load re-downloads it.

        Returns True when at least one model cache subdir existed and was removed.
        """
        removed = False
        for subdir in self._model_cache_subdirs():
            if subdir.exists():
                logger.warning(
                    "Removing corrupt FastEmbed model cache to force re-download: {path}",
                    path=str(subdir),
                )
                shutil.rmtree(subdir, ignore_errors=True)
                removed = True
        return removed

    @staticmethod
    def _is_corrupt_model_error(exc: Exception) -> bool:
        """Return True when the load failure looks like a missing/corrupt model artifact."""
        message = str(exc).lower()
        return any(marker in message for marker in _CORRUPT_MODEL_ERROR_MARKERS)

    async def _load_model(self) -> "TextEmbedding":
        if self._model is not None:
            return self._model

        async with self._model_lock:
            if self._model is not None:
                return self._model

            try:
                self._model = await asyncio.to_thread(self._create_model)
            except Exception as exc:
                # Trigger: model construction failed with a missing/corrupt-artifact error
                #          (an interrupted download left a partial snapshot in the cache).
                # Why: the raw ONNXRuntimeError is self-perpetuating — every retry hits the
                #      same broken snapshot until the cache is cleared. Scope the deletion to
                #      this model's own ``models--...`` subdir and retry exactly once so a
                #      fresh download can land. A single retry avoids an infinite re-download
                #      loop if the failure is not actually a cache problem.
                # Outcome: on success the user transparently recovers; on a second failure we
                #          fail fast with the original error so the message stays actionable.
                if not self._is_corrupt_model_error(exc):
                    raise
                if not self._purge_corrupt_model_cache():
                    raise
                logger.info(
                    "Retrying FastEmbed model load after clearing corrupt cache: "
                    "model_name={model_name}",
                    model_name=self._MODEL_ALIASES.get(self.model_name, self.model_name),
                )
                self._model = await asyncio.to_thread(self._create_model)

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
            # sqlite_search_repository.py uses a distance-to-similarity formula that assumes
            # unit-normalized vectors (see the comment on line 65-67 of that file).
            # Some models (e.g. multilingual ones) return vectors with norm > 1, so we
            # L2-normalize here to satisfy that contract regardless of the chosen model.
            normalized: list[list[float]] = []
            for vector in vectors:
                values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
                norm = math.sqrt(sum(x * x for x in values))
                if norm > 0:
                    values = [x / norm for x in values]
                normalized.append([float(v) for v in values])
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
