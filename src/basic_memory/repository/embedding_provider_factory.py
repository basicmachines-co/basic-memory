"""Factory for creating configured semantic embedding providers."""

import os
from threading import Lock

from basic_memory.config import BasicMemoryConfig, default_fastembed_cache_dir
from basic_memory.repository.embedding_provider import EmbeddingProvider

type ProviderCacheKey = tuple[
    str,
    str,
    int | None,
    int,
    int,
    str,
    int | None,
    int | None,
]

_EMBEDDING_PROVIDER_CACHE: dict[ProviderCacheKey, EmbeddingProvider] = {}
_EMBEDDING_PROVIDER_CACHE_LOCK = Lock()
_FASTEMBED_MAX_THREADS = 8


def _resolve_cache_dir(app_config: BasicMemoryConfig) -> str:
    """Resolve the effective FastEmbed cache dir for this config.

    Uses an explicit ``is not None`` check — an empty string override from
    config or ``BASIC_MEMORY_SEMANTIC_EMBEDDING_CACHE_DIR`` is an invalid
    path, not a request to fall back to the default, and FastEmbed's error
    message is clearer than silently swapping in a different directory.
    """
    configured = app_config.semantic_embedding_cache_dir
    if configured is not None:
        return configured
    return default_fastembed_cache_dir()


def _available_cpu_count() -> int | None:
    """Return the CPU budget available to this process when the runtime exposes it."""
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if callable(process_cpu_count):
        cpu_count = process_cpu_count()
        if isinstance(cpu_count, int) and cpu_count > 0:
            return cpu_count

    cpu_count = os.cpu_count()
    return cpu_count if cpu_count is not None and cpu_count > 0 else None


def _resolve_fastembed_runtime_knobs(
    app_config: BasicMemoryConfig,
) -> tuple[int | None, int | None]:
    """Resolve FastEmbed threads/parallel from explicit config or CPU-aware defaults."""
    configured_threads = app_config.semantic_embedding_threads
    configured_parallel = app_config.semantic_embedding_parallel
    if configured_threads is not None or configured_parallel is not None:
        return configured_threads, configured_parallel

    available_cpus = _available_cpu_count()
    if available_cpus is None:
        return None, None

    # Trigger: local laptops and cloud workers expose different CPU budgets.
    # Why: full rebuilds got faster when FastEmbed used most, but not all, of
    # the available CPUs. Leaving a little headroom avoids starving the rest of
    # the pipeline while still giving ONNX enough threads to stay busy.
    # Outcome: when config leaves the knobs unset, each process reserves a small
    # CPU cushion and keeps FastEmbed on the simpler single-process path.
    if available_cpus <= 2:
        return available_cpus, 1

    threads = min(_FASTEMBED_MAX_THREADS, max(2, available_cpus - 2))
    return threads, 1


def _provider_cache_key(app_config: BasicMemoryConfig) -> ProviderCacheKey:
    """Build a stable cache key from provider-relevant semantic embedding config.

    Uses the *resolved* cache dir — not the raw config field — so different
    FASTEMBED_CACHE_PATH values produce distinct cache keys even when the
    config field itself is unset.
    """
    resolved_threads, resolved_parallel = _resolve_fastembed_runtime_knobs(app_config)
    return (
        app_config.semantic_embedding_provider.strip().lower(),
        app_config.semantic_embedding_model,
        app_config.semantic_embedding_dimensions,
        app_config.semantic_embedding_batch_size,
        app_config.semantic_embedding_request_concurrency,
        _resolve_cache_dir(app_config),
        resolved_threads,
        resolved_parallel,
    )


def reset_embedding_provider_cache() -> None:
    """Clear process-level embedding provider cache (used by tests)."""
    with _EMBEDDING_PROVIDER_CACHE_LOCK:
        _EMBEDDING_PROVIDER_CACHE.clear()


def create_embedding_provider(app_config: BasicMemoryConfig) -> EmbeddingProvider:
    """Create an embedding provider based on semantic config.

    When semantic_embedding_dimensions is set in config, it overrides
    the provider's default dimensions (384 for FastEmbed, 1536 for OpenAI).
    """
    cache_key = _provider_cache_key(app_config)
    with _EMBEDDING_PROVIDER_CACHE_LOCK:
        if cached_provider := _EMBEDDING_PROVIDER_CACHE.get(cache_key):
            return cached_provider

    provider_name = app_config.semantic_embedding_provider.strip().lower()
    extra_kwargs: dict = {}
    if app_config.semantic_embedding_dimensions is not None:
        extra_kwargs["dimensions"] = app_config.semantic_embedding_dimensions

    provider: EmbeddingProvider
    if provider_name == "fastembed":
        # Deferred import: fastembed (and its onnxruntime dep) may not be installed
        from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider

        resolved_threads, resolved_parallel = _resolve_fastembed_runtime_knobs(app_config)
        # Trigger: cache_dir is resolved rather than passed through directly.
        # Why: FastEmbed's own default caches to <system tmp>/fastembed_cache,
        #      which disappears in sandboxed MCP runtimes (e.g. Codex CLI). See #741.
        # Outcome: always pass an explicit, user-writable cache dir so the ONNX
        #          model persists across runs.
        extra_kwargs["cache_dir"] = _resolve_cache_dir(app_config)
        if resolved_threads is not None:
            extra_kwargs["threads"] = resolved_threads
        if resolved_parallel is not None:
            extra_kwargs["parallel"] = resolved_parallel

        provider = FastEmbedEmbeddingProvider(
            model_name=app_config.semantic_embedding_model,
            batch_size=app_config.semantic_embedding_batch_size,
            **extra_kwargs,
        )
    elif provider_name == "openai":
        # Deferred import: openai may not be installed
        from basic_memory.repository.openai_provider import OpenAIEmbeddingProvider

        model_name = app_config.semantic_embedding_model or "text-embedding-3-small"
        if model_name == "bge-small-en-v1.5":
            model_name = "text-embedding-3-small"
        provider = OpenAIEmbeddingProvider(
            model_name=model_name,
            batch_size=app_config.semantic_embedding_batch_size,
            request_concurrency=app_config.semantic_embedding_request_concurrency,
            **extra_kwargs,
        )
    else:
        raise ValueError(f"Unsupported semantic embedding provider: {provider_name}")

    with _EMBEDDING_PROVIDER_CACHE_LOCK:
        if cached_provider := _EMBEDDING_PROVIDER_CACHE.get(cache_key):
            return cached_provider
        _EMBEDDING_PROVIDER_CACHE[cache_key] = provider
        return provider
