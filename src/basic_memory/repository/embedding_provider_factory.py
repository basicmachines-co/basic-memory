"""Factory for creating configured semantic embedding providers."""

import hashlib
import os
from threading import Lock

from loguru import logger

from basic_memory.config import BasicMemoryConfig, default_fastembed_cache_dir
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.prefixing_provider import (
    PrefixingEmbeddingProvider,
    embedding_prefix_digest,
    normalize_embedding_prefix,
)

# Cache key fields are limited to values that change the *identity* of the loaded
# provider instance (provider, model_name, explicit LiteLLM endpoint/key routing,
# dimensions, semantic role/input-type/prefix settings, batch/request knobs,
# and the resolved cache dir). Thread/parallel knobs are deliberately excluded -
# they change ONNX *execution* only, not the loaded weights. Including them caused #872: in a
# container/cgroup the CPU-derived thread count can drift between calls, producing
# a fresh cache key and reloading the ~2.3GB model into a CPU arena that never
# returns memory to the OS.
type ProviderCacheKey = tuple[
    str,
    str,
    str | None,
    str | None,
    int | None,
    bool | None,
    int,
    int,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
]

_EMBEDDING_PROVIDER_CACHE: dict[ProviderCacheKey, EmbeddingProvider] = {}
_EMBEDDING_PROVIDER_CACHE_LOCK = Lock()
_FASTEMBED_MAX_THREADS = 8


def _sensitive_value_digest(value: str | None) -> str | None:
    """Return a stable non-secret token for process-local cache diagnostics."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolve_cache_dir(app_config: BasicMemoryConfig) -> str:
    """Resolve the effective FastEmbed cache dir for this config.

    Uses an explicit ``is not None`` check - an empty string override from
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
    """Build a stable cache key from process-local embedding provider config.

    Uses the *resolved* cache dir - not the raw config field - so different
    FASTEMBED_CACHE_PATH values produce distinct cache keys even when the
    config field itself is unset.

    Deliberately excludes the FastEmbed thread/parallel knobs: they tune ONNX
    execution, not which model weights are loaded, and resolving them from the
    runtime CPU budget makes the key drift between calls in a container (#872).
    """
    provider_name = app_config.semantic_embedding_provider.strip().lower()
    litellm_api_base_digest = None
    litellm_api_key_digest = None
    if provider_name == "litellm":
        litellm_api_base_digest = _sensitive_value_digest(app_config.semantic_embedding_api_base)
        litellm_api_key_digest = _sensitive_value_digest(app_config.semantic_embedding_api_key)

    return (
        provider_name,
        app_config.semantic_embedding_model,
        litellm_api_base_digest,
        litellm_api_key_digest,
        app_config.semantic_embedding_dimensions,
        app_config.semantic_embedding_forward_dimensions,
        app_config.semantic_embedding_batch_size,
        app_config.semantic_embedding_request_concurrency,
        app_config.semantic_embedding_document_input_type,
        app_config.semantic_embedding_query_input_type,
        embedding_prefix_digest(app_config.semantic_embedding_document_prefix),
        embedding_prefix_digest(app_config.semantic_embedding_query_prefix),
        _resolve_cache_dir(app_config),
    )


def reset_embedding_provider_cache() -> None:
    """Clear process-level embedding provider cache (used by tests)."""
    with _EMBEDDING_PROVIDER_CACHE_LOCK:
        _EMBEDDING_PROVIDER_CACHE.clear()


def create_embedding_provider(app_config: BasicMemoryConfig) -> EmbeddingProvider:
    """Create an embedding provider based on semantic config.

    When semantic_embedding_dimensions is set in config, it overrides the
    provider's default dimensions (384 for FastEmbed, 1536 for OpenAI and
    the LiteLLM OpenAI default). Custom LiteLLM models require an explicit
    dimension because the vector table schema is created before the first
    embedding response is available.
    """
    cache_key = _provider_cache_key(app_config)
    # Trigger: two threads miss the cache for the same key concurrently.
    # Why: provider construction loads the ~2.3GB ONNX model and is slow, so we
    # deliberately build it *outside* the lock to avoid serializing every caller
    # behind a single cold start. This opens a by-design TOCTOU window where both
    # threads may construct a provider.
    # Outcome: the second check-and-set below resolves the race - the first writer
    # wins and the loser's redundant provider is discarded, so the cache still
    # yields a single process-wide singleton per key.
    with _EMBEDDING_PROVIDER_CACHE_LOCK:
        if cached_provider := _EMBEDDING_PROVIDER_CACHE.get(cache_key):
            return cached_provider

    extra_kwargs: dict = {}
    if app_config.semantic_embedding_dimensions is not None:
        extra_kwargs["dimensions"] = app_config.semantic_embedding_dimensions

    provider: EmbeddingProvider
    provider_name = app_config.semantic_embedding_provider.strip().lower()
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
    elif provider_name == "litellm":
        from basic_memory.repository.litellm_provider import LiteLLMEmbeddingProvider

        model_name = app_config.semantic_embedding_model or "openai/text-embedding-3-small"
        if model_name == "bge-small-en-v1.5":
            model_name = "openai/text-embedding-3-small"
        if (
            app_config.semantic_embedding_dimensions is None
            and model_name != "openai/text-embedding-3-small"
        ):
            raise ValueError(
                "semantic_embedding_dimensions must be set when "
                "semantic_embedding_provider='litellm' uses a non-default model. "
                f"Configured model: {model_name!r}."
            )
        provider = LiteLLMEmbeddingProvider(
            model_name=model_name,
            api_key=app_config.semantic_embedding_api_key,
            api_base=app_config.semantic_embedding_api_base,
            batch_size=app_config.semantic_embedding_batch_size,
            request_concurrency=app_config.semantic_embedding_request_concurrency,
            document_input_type=app_config.semantic_embedding_document_input_type,
            query_input_type=app_config.semantic_embedding_query_input_type,
            forward_dimensions=app_config.semantic_embedding_forward_dimensions,
            **extra_kwargs,
        )
    else:
        raise ValueError(f"Unsupported semantic embedding provider: {provider_name}")

    document_prefix = normalize_embedding_prefix(app_config.semantic_embedding_document_prefix)
    query_prefix = normalize_embedding_prefix(app_config.semantic_embedding_query_prefix)
    if document_prefix is not None or query_prefix is not None:
        provider = PrefixingEmbeddingProvider(
            provider,
            document_prefix=document_prefix,
            query_prefix=query_prefix,
        )

    with _EMBEDDING_PROVIDER_CACHE_LOCK:
        if cached_provider := _EMBEDDING_PROVIDER_CACHE.get(cache_key):
            return cached_provider
        # Trigger: a distinct cache key is being inserted while the cache already
        # holds entries for other keys.
        # Why: the provider is meant to be a process-wide singleton (#872). A second
        # key means something bypassed reuse - a real config change, or a regression
        # that reintroduces volatile fields into the key - and each new key reloads
        # the ~2.3GB ONNX model into a CPU arena that never releases memory.
        # Outcome: surface the bypass so future leaks are diagnosable from logs.
        if _EMBEDDING_PROVIDER_CACHE:
            logger.warning(
                "Creating a second distinct embedding provider in this process; "
                "the model will be loaded again. existing_keys={existing} new_key={new}",
                existing=list(_EMBEDDING_PROVIDER_CACHE.keys()),
                new=cache_key,
            )
        _EMBEDDING_PROVIDER_CACHE[cache_key] = provider
        return provider
