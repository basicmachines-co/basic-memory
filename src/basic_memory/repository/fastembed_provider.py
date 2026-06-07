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


# Substrings that identify the ONNX "model artifact file is missing" load failure (as
# opposed to a config error, a download/network error, or a genuinely offline machine).
# An interrupted FastEmbed download can leave the HuggingFace snapshot dir present but
# missing ``model_optimized.onnx``; the ONNX runtime then raises ``NO_SUCHFILE`` and every
# subsequent load repeats it until the cache is cleared. Matched case-insensitively.
#
# IMPORTANT: this text match is necessary but NOT sufficient to trigger a purge. The error
# text alone cannot distinguish a corrupt cache from a normal cold load (model not yet
# downloaded). Purging is gated on a positive filesystem confirmation that the snapshot dir
# exists on disk but the model artifact file is missing — see ``_corrupt_model_subdirs``.
_MISSING_ARTIFACT_ERROR_MARKERS = (
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

    def _resolved_model_name(self) -> str:
        """Return the FastEmbed model name after applying our local aliases."""
        return self._MODEL_ALIASES.get(self.model_name, self.model_name)

    def _create_model(self) -> "TextEmbedding":
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - exercised via tests with monkeypatch
            raise SemanticDependenciesMissingError(
                "fastembed package is missing. "
                "Install/update basic-memory to include semantic dependencies: "
                "pip install -U basic-memory"
            ) from exc
        resolved_model_name = self._resolved_model_name()
        # Constraint: onnxruntime's CPU memory arena grows to fit peak usage and never
        # returns that memory to the OS. If a model is ever loaded more than once in a
        # long-running process it leaks tens of GB (#872). FastEmbed exposes
        # enable_cpu_mem_arena via its session-option kwargs, so we disable the arena to
        # let any transient extra load free memory.
        model_kwargs: dict = {
            "model_name": resolved_model_name,
            "enable_cpu_mem_arena": False,
        }
        if self.cache_dir is not None:
            model_kwargs["cache_dir"] = self.cache_dir
        if self.threads is not None:
            model_kwargs["threads"] = self.threads
        return TextEmbedding(**model_kwargs)

    def _model_cache_candidates(self) -> list[tuple[Path, str]]:
        """Resolve ``(snapshot_dir, model_file)`` pairs for this model under ``cache_dir``.

        FastEmbed stores each model under ``<cache_dir>/models--<org>--<repo>`` where the
        repo is the model's HuggingFace source (e.g. ``BAAI/bge-small-en-v1.5`` resolves to
        ``models--qdrant--bge-small-en-v1.5-onnx-q``). We resolve the source and the expected
        model artifact filename from FastEmbed's own model description so corruption detection
        and deletion are scoped to exactly this model's tree — never the whole cache or
        unrelated models.

        Note: ``TextEmbedding._list_supported_models()`` is an intentional use of an
        undocumented FastEmbed API. The broad ``except`` below is a known defensive fallback:
        if the lookup ever changes shape we degrade to "no candidates" (so we never purge)
        rather than crashing the load path.
        """
        if self.cache_dir is None:
            return []

        # FastEmbed matches model names case-insensitively (model_management.py:
        # ``model_name.lower() == model.model.lower()``). Mirror that here so a config like
        # model="baai/bge-small-en-v1.5" still resolves to the same HF source/cache subdir.
        resolved_model_name = self._resolved_model_name().lower()
        candidates: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        cache_root = Path(self.cache_dir)
        try:
            from fastembed import TextEmbedding

            for description in TextEmbedding._list_supported_models():
                if description.model.lower() != resolved_model_name:
                    continue
                hf_source = description.sources.hf
                model_file = description.model_file
                if not hf_source or not model_file:
                    continue
                # HuggingFace hub names cache dirs ``models--<repo with '/' -> '--'>``.
                snapshot_dir = cache_root / f"models--{hf_source.replace('/', '--')}"
                if snapshot_dir not in seen:
                    seen.add(snapshot_dir)
                    candidates.append((snapshot_dir, model_file))
        except Exception as exc:  # pragma: no cover - defensive: never block load on lookup
            logger.warning(
                "Could not resolve FastEmbed model source for cache cleanup: "
                "model_name={model_name} error={error}",
                model_name=resolved_model_name,
                error=exc,
            )

        return candidates

    def _corrupt_model_subdirs(self) -> list[Path]:
        """Return cache subdirs that are POSITIVELY confirmed corrupt by filesystem state.

        A model is corrupt when its HuggingFace cache dir exists on disk but at least one
        materialized snapshot revision is missing the expected model artifact file (e.g.
        ``model_optimized.onnx``) — the exact fingerprint of an interrupted download. A normal
        cold load (no cache dir yet) is NOT corruption and yields no entries here, so it can
        never trigger a purge.

        Inspection is PER-REVISION on purpose: HuggingFace keeps multiple revisions under one
        ``models--<repo>`` tree, so a corrupt current snapshot can coexist with an older
        complete one. Checking ``rglob(model_file)`` across the whole tree would let the old
        artifact mask the broken current revision and leave it self-perpetuating, so we
        require every revision to carry the artifact.
        """
        corrupt: list[Path] = []
        for model_dir, model_file in self._model_cache_candidates():
            # Trigger: the model's cache dir does not exist at all.
            # Why: this is a normal cold/first load — the model simply hasn't been
            #      downloaded yet. Purging here would be wrong and pointless.
            # Outcome: skip; not corrupt.
            if not model_dir.exists():
                continue
            snapshots_root = model_dir / "snapshots"
            revision_dirs = (
                [d for d in snapshots_root.iterdir() if d.is_dir()]
                if snapshots_root.is_dir()
                else []
            )
            # Trigger: the cache dir exists but no snapshot revision has materialized.
            # Why/Outcome: an interrupted download that never wrote a revision — corrupt.
            if not revision_dirs:
                corrupt.append(model_dir)
                continue
            # Trigger: any individual revision is missing the artifact (rglob covers the
            # artifact at any depth within that revision, e.g. snapshots/<rev>/onnx/...).
            # Why: a complete OLD revision must not mask a corrupt CURRENT one.
            # Outcome: flag the model dir so the whole tree re-downloads cleanly.
            if any(not any(rev.rglob(model_file)) for rev in revision_dirs):
                corrupt.append(model_dir)
        return corrupt

    def _purge_model_subdirs(self, subdirs: list[Path]) -> bool:
        """Delete confirmed-corrupt cache subtrees so the next load re-downloads them.

        Returns True when at least one targeted subdir is actually gone afterwards. On
        Windows a locked file can make ``shutil.rmtree(ignore_errors=True)`` silently no-op;
        reporting success in that case would let the caller retry against the same broken
        cache, so each subdir only counts as removed once it has actually disappeared.
        """
        removed_any = False
        for subdir in subdirs:
            logger.warning(
                "Removing corrupt FastEmbed model cache to force re-download: {path}",
                path=str(subdir),
            )
            shutil.rmtree(subdir, ignore_errors=True)
            # Set removed only when the subdir is truly gone — a silent rmtree no-op
            # (e.g. a locked file on Windows) must not be reported as a successful purge.
            if not subdir.exists():
                removed_any = True
        return removed_any

    @staticmethod
    def _is_missing_artifact_error(exc: Exception) -> bool:
        """Return True when the load failure text matches the ONNX missing-artifact signature.

        This is only the text-level gate; it is necessary but NOT sufficient to purge. The
        purge additionally requires filesystem-confirmed corruption (``_corrupt_model_subdirs``)
        so a transient/offline/"from any source" load error never deletes a valid cache.
        """
        message = str(exc).lower()
        return any(marker in message for marker in _MISSING_ARTIFACT_ERROR_MARKERS)

    async def _load_model(self) -> "TextEmbedding":
        if self._model is not None:
            return self._model

        async with self._model_lock:
            if self._model is not None:
                return self._model

            try:
                self._model = await asyncio.to_thread(self._create_model)
            except Exception as exc:
                # Trigger: model construction raised the ONNX missing-artifact error AND a
                #          filesystem check positively confirms a corrupt cache subdir (the
                #          snapshot dir exists but the model artifact file is missing — the
                #          fingerprint of an interrupted download).
                # Why: the raw ONNXRuntimeError is self-perpetuating — every retry hits the
                #      same broken snapshot until the cache is cleared. We must NOT misread a
                #      normal cold load (no snapshot dir, model simply not downloaded yet) or a
                #      transient/offline "from any source" error as corruption, because purging
                #      then breaks the happy path. Both the error-text gate and the positive
                #      filesystem confirmation are required before we delete anything.
                # Outcome: confirmed corruption → purge exactly this model's subdir and retry
                #          once so a fresh download can land. Every other failure (including a
                #          retry that still fails) re-raises the ORIGINAL exception so the
                #          message stays actionable and we never loop.
                if not self._is_missing_artifact_error(exc):
                    raise
                corrupt_subdirs = self._corrupt_model_subdirs()
                if not corrupt_subdirs:
                    raise
                if not self._purge_model_subdirs(corrupt_subdirs):
                    raise
                logger.info(
                    "Retrying FastEmbed model load after clearing corrupt cache: "
                    "model_name={model_name}",
                    model_name=self._resolved_model_name(),
                )
                self._model = await asyncio.to_thread(self._create_model)

            logger.info(
                "FastEmbed model loaded: model_name={model_name} batch_size={batch_size} "
                "threads={threads} configured_parallel={configured_parallel} "
                "effective_parallel={effective_parallel}",
                model_name=self._resolved_model_name(),
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
