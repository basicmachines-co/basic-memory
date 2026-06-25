"""Basic Memory cloud provider (optional)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from basic_memory_benchmarks.exceptions import ProviderSkippedError
from basic_memory_benchmarks.models import RunConfig, SearchHit
from basic_memory_benchmarks.providers.base import BenchmarkProvider
from basic_memory_benchmarks.utils import run_command


class BasicMemoryCloudProvider(BenchmarkProvider):
    name = "bm-cloud"

    def _enabled(self) -> bool:
        return os.getenv("BASIC_MEMORY_BENCH_ENABLE_CLOUD", "").lower() in {"1", "true", "yes"}

    def _project_name(self, run_config: RunConfig) -> str:
        return f"bm-bench-{run_config.run_id}"

    def ingest(self, corpus_path: Path, run_config: RunConfig) -> None:
        _ = corpus_path
        _ = run_config
        if not self._enabled():
            raise ProviderSkippedError(
                "Cloud benchmark disabled. Set BASIC_MEMORY_BENCH_ENABLE_CLOUD=true to enable."
            )

    def search(self, query: str, limit: int, run_config: RunConfig) -> list[SearchHit]:
        if not self._enabled():
            raise ProviderSkippedError(
                "Cloud benchmark disabled. Set BASIC_MEMORY_BENCH_ENABLE_CLOUD=true to enable."
            )

        project_name = self._project_name(run_config)
        completed = run_command(
            [
                "bm",
                "tool",
                "search-notes",
                query,
                "--project",
                project_name,
                "--page-size",
                str(limit),
                "--hybrid",
                "--cloud",
            ]
        )
        payload = json.loads(completed.stdout.strip() or "{}")
        rows = payload.get("results") if isinstance(payload, dict) else []

        hits: list[SearchHit] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            raw_path = row.get("file_path") or row.get("permalink")
            doc_id = None
            if raw_path:
                tail = str(raw_path).rstrip("/").split("/")[-1]
                doc_id = tail[:-3] if tail.endswith(".md") else tail
            metadata_raw = row.get("metadata")
            metadata: dict[str, Any]
            if isinstance(metadata_raw, dict):
                metadata = cast(dict[str, Any], metadata_raw)
            else:
                metadata = {}
            hits.append(
                SearchHit(
                    id=str(row.get("entity_id") or ""),
                    source_doc_id=doc_id,
                    source_path=raw_path,
                    text=row.get("matched_chunk") or row.get("content"),
                    score=float(row.get("score", 0.0) or 0.0),
                    metadata=metadata,
                )
            )
        return hits

    def cleanup(self, run_config: RunConfig) -> None:
        _ = run_config

    def version_info(self) -> dict[str, str]:
        return {}
