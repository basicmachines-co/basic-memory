"""Provider factory."""

from __future__ import annotations

from basic_memory_benchmarks.providers.base import BenchmarkProvider
from basic_memory_benchmarks.providers.baseline_fullcontext import FullContextProvider
from basic_memory_benchmarks.providers.baseline_grep import FilesystemGrepProvider
from basic_memory_benchmarks.providers.bm_cloud import BasicMemoryCloudProvider
from basic_memory_benchmarks.providers.bm_local import BasicMemoryLocalProvider
from basic_memory_benchmarks.providers.mem0_local import Mem0LocalProvider
from basic_memory_benchmarks.providers.supermemory_local import SupermemoryLocalProvider
from basic_memory_benchmarks.providers.zep_reference import ZepReferenceProvider


def create_provider(name: str) -> BenchmarkProvider:
    normalized = name.strip().lower()
    if normalized == "bm-local":
        return BasicMemoryLocalProvider()
    if normalized == "bm-cloud":
        return BasicMemoryCloudProvider()
    if normalized == "mem0-local":
        return Mem0LocalProvider()
    if normalized == "zep-reference":
        return ZepReferenceProvider()
    if normalized == "baseline-grep":
        return FilesystemGrepProvider()
    if normalized == "baseline-fullcontext":
        return FullContextProvider()
    if normalized == "supermemory-local":
        return SupermemoryLocalProvider()
    raise ValueError(f"Unknown provider: {name}")


def provider_names() -> list[str]:
    return [
        "bm-local",
        "bm-cloud",
        "mem0-local",
        "zep-reference",
        "baseline-grep",
        "baseline-fullcontext",
        "supermemory-local",
    ]
