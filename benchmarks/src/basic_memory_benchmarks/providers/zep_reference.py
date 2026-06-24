"""Reference-only Zep provider.

This provider is intentionally non-live for v1. It exists to keep report schema stable
for externally published benchmark references.
"""

from __future__ import annotations

from basic_memory_benchmarks.exceptions import ProviderSkippedError
from basic_memory_benchmarks.models import RunConfig, SearchHit
from basic_memory_benchmarks.providers.base import BenchmarkProvider


class ZepReferenceProvider(BenchmarkProvider):
    name = "zep-reference"

    def ingest(self, corpus_path, run_config: RunConfig) -> None:
        _ = corpus_path
        _ = run_config

    def search(self, query: str, limit: int, run_config: RunConfig) -> list[SearchHit]:
        _ = query
        _ = limit
        _ = run_config
        raise ProviderSkippedError("Zep is reference-only in v1 and does not execute live queries")

    def cleanup(self, run_config: RunConfig) -> None:
        _ = run_config
