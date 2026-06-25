"""Mem0 local provider using the mem0ai package.

Model backends, in priority order:

1. **Local OpenAI-compatible endpoint** (zero API spend): set
   ``MEM0_OPENAI_COMPAT_BASE_URL`` (e.g. ``http://localhost:11434/v1`` for
   Ollama). LLM and embeddings both route there via mem0's ``openai``
   provider, and the qdrant store is configured to the local embedder's
   dimensions under a benchmark-scoped on-disk path.
2. **OpenAI** (mem0's defaults): ``OPENAI_API_KEY`` set, no base-url override.

With neither configured the provider is skipped.
"""

from __future__ import annotations

import os
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

import frontmatter

from basic_memory_benchmarks.exceptions import ProviderSkippedError
from basic_memory_benchmarks.models import RunConfig, SearchHit
from basic_memory_benchmarks.providers.base import BenchmarkProvider

# Defaults for the local backend; override via env.
_DEFAULT_LOCAL_LLM_MODEL = "qwen2.5:3b"
_DEFAULT_LOCAL_EMBED_MODEL = "nomic-embed-text"
_DEFAULT_LOCAL_EMBED_DIMS = 768


class Mem0LocalProvider(BenchmarkProvider):
    name = "mem0-local"

    def __init__(self) -> None:
        self._memory = None
        self._backend: str | None = None
        self._infer: bool = os.getenv("MEM0_INFER", "false").strip().lower() == "true"

    def _user_id(self, run_config: RunConfig) -> str:
        return f"bm-bench-{run_config.run_id}-mem0"

    def _local_config(self, base_url: str, run_config: RunConfig) -> dict:
        api_key = os.getenv("MEM0_OPENAI_COMPAT_API_KEY", "local")
        embed_dims = int(os.getenv("MEM0_EMBED_DIMS", str(_DEFAULT_LOCAL_EMBED_DIMS)))
        qdrant_root = os.getenv("MEM0_QDRANT_PATH", "benchmarks/.mem0-qdrant")
        # Collection + path are run-scoped: local embedding dims differ from
        # OpenAI's, and qdrant rejects dim changes within a collection.
        collection = f"bm_bench_{run_config.run_id}".replace("-", "_")
        return {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": os.getenv("MEM0_LLM_MODEL", _DEFAULT_LOCAL_LLM_MODEL),
                    "openai_base_url": base_url,
                    "api_key": api_key,
                    "temperature": 0,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": os.getenv("MEM0_EMBED_MODEL", _DEFAULT_LOCAL_EMBED_MODEL),
                    "openai_base_url": base_url,
                    "api_key": api_key,
                    "embedding_dims": embed_dims,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": collection,
                    "embedding_model_dims": embed_dims,
                    "path": str(Path(qdrant_root) / collection),
                },
            },
        }

    def _ensure_memory(self, run_config: RunConfig):
        if self._memory is not None:
            return self._memory

        # Trigger: mem0 with MEM0_TELEMETRY on (its default) opens a qdrant
        # client at a FIXED path (~/.mem0/migrations_qdrant) inside every
        # Memory(); qdrant local mode allows one client per path per process,
        # so the second provider instance in a grouped run dies with
        # "Storage folder ... is already accessed" (matrix v1: 24/25 LME and
        # 30/30 ConvoMem groups lost).
        # Why: benchmark runs should not emit telemetry anyway.
        # Outcome: telemetry store never created; operator can force-enable.
        os.environ.setdefault("MEM0_TELEMETRY", "false")

        from mem0 import Memory  # Deferred import to keep startup lightweight

        base_url = os.getenv("MEM0_OPENAI_COMPAT_BASE_URL")
        if base_url:
            self._backend = f"openai-compat:{base_url}"
            self._memory = Memory.from_config(self._local_config(base_url, run_config))
        elif os.getenv("OPENAI_API_KEY"):
            self._backend = "openai-default"
            self._memory = Memory()
        else:
            raise ProviderSkippedError(
                "mem0-local needs MEM0_OPENAI_COMPAT_BASE_URL (local endpoint) or OPENAI_API_KEY"
            )
        return self._memory

    @staticmethod
    def _doc_id_from_path(path: Path) -> str:
        if path.suffix == ".md":
            return path.name[:-3]
        return path.name

    @staticmethod
    def _conversation_id(path: Path) -> str:
        parts = list(path.parts)
        for part in parts:
            if part.startswith("locomo-c"):
                return part
        return "unknown"

    def ingest(self, corpus_path: Path, run_config: RunConfig) -> None:
        memory = self._ensure_memory(run_config)
        user_id = self._user_id(run_config)

        for note_path in sorted(corpus_path.rglob("*.md")):
            rel_path = note_path.relative_to(corpus_path).as_posix()
            with note_path.open("r", encoding="utf-8") as handle:
                parsed = frontmatter.load(handle)
            doc_id = str(parsed.get("source_doc_id") or self._doc_id_from_path(note_path))
            conversation_id = str(parsed.get("conversation_id") or self._conversation_id(note_path))
            metadata = {
                "source_doc_id": doc_id,
                "source_path": rel_path,
                "conversation_id": conversation_id,
                "dataset_id": run_config.dataset_id,
            }
            memory.add(parsed.content, user_id=user_id, metadata=metadata, infer=self._infer)

    @staticmethod
    def _normalize_item(item: dict) -> SearchHit:
        metadata_raw = item.get("metadata")
        metadata: dict[str, Any]
        if isinstance(metadata_raw, dict):
            metadata = cast(dict[str, Any], metadata_raw)
        else:
            metadata = {}
        source_doc_id = item.get("source_doc_id") or metadata.get("source_doc_id")
        source_path = item.get("source_path") or metadata.get("source_path")
        score_raw = item.get("score")
        try:
            score = float(score_raw) if score_raw is not None else None
        except (TypeError, ValueError):
            score = None

        return SearchHit(
            id=str(item.get("id") or ""),
            source_doc_id=source_doc_id,
            source_path=source_path,
            text=item.get("memory") or item.get("text"),
            score=score,
            metadata=metadata,
        )

    def search(self, query: str, limit: int, run_config: RunConfig) -> list[SearchHit]:
        memory = self._ensure_memory(run_config)
        user_id = self._user_id(run_config)
        # mem0ai 2.0: entity scoping moved from top-level user_id= to filters=,
        # and limit= became top_k=.
        payload = memory.search(query=query, top_k=limit, filters={"user_id": user_id})
        rows = payload.get("results") if isinstance(payload, dict) else []

        hits: list[SearchHit] = []
        for item in rows or []:
            if isinstance(item, dict):
                hits.append(self._normalize_item(item))
        return hits

    def cleanup(self, run_config: RunConfig) -> None:
        if self._memory is None:
            return
        try:
            self._memory.delete_all(user_id=self._user_id(run_config))
        except Exception:
            # Cleanup should never break the main benchmark flow.
            pass
        # Release qdrant local-mode file locks even while this instance is
        # still referenced (the grouped runner keeps the last provider for
        # version_info), or the next instance cannot open its stores.
        for store_attr in ("vector_store", "_telemetry_vector_store"):
            client = getattr(getattr(self._memory, store_attr, None), "client", None)
            if client is not None and hasattr(client, "close"):
                try:
                    client.close()
                except Exception:
                    pass
        self._memory = None

    def version_info(self) -> dict[str, str]:
        metadata: dict[str, str] = {
            "mem0_backend": self._backend or "unconfigured",
            "mem0_infer": "true" if self._infer else "false",
        }
        if self._backend and self._backend.startswith("openai-compat"):
            metadata["mem0_llm_model"] = os.getenv("MEM0_LLM_MODEL", _DEFAULT_LOCAL_LLM_MODEL)
            metadata["mem0_embed_model"] = os.getenv("MEM0_EMBED_MODEL", _DEFAULT_LOCAL_EMBED_MODEL)
        try:
            metadata["mem0ai"] = version("mem0ai")
        except Exception:
            pass
        return metadata
