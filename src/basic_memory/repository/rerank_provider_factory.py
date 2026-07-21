"""Factory for creating configured reranker providers.

Mirrors ``embedding_provider_factory``: string-dispatch on ``reranker_provider``
plus a process-wide singleton cache so the cross-encoder model loads once. The
fastembed path reuses the embedding provider's resolved cache dir and CPU-aware
thread budget — reranker and embedder models live in the same cache under
distinct model subdirs.
"""

from threading import Lock

from loguru import logger

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import (
    _resolve_cache_dir,
    _resolve_fastembed_runtime_knobs,
    _sensitive_value_digest,
)
from basic_memory.repository.rerank_provider import RerankProvider

# Key on the fields that change the loaded provider's identity: provider, model,
# (for the litellm path) the endpoint/key routing, and the resolved cache dir. The
# cache dir matters because the fastembed provider is constructed with it — omitting
# it (as an earlier version did) lets two configs with different cache dirs share one
# singleton pointing at the wrong directory, the #741/#872 class of bug the embedding
# factory guards against. CPU-derived thread counts stay out (they drift per call).
type RerankCacheKey = tuple[str, str, str | None, str | None, str]

_RERANK_PROVIDER_CACHE: dict[RerankCacheKey, RerankProvider] = {}
_RERANK_PROVIDER_CACHE_LOCK = Lock()


def _rerank_cache_key(app_config: BasicMemoryConfig) -> RerankCacheKey:
    provider_name = app_config.reranker_provider.strip().lower()
    api_base_digest = None
    api_key_digest = None
    if provider_name == "litellm":
        api_base_digest = _sensitive_value_digest(app_config.reranker_api_base)
        api_key_digest = _sensitive_value_digest(app_config.reranker_api_key)
    return (
        provider_name,
        app_config.reranker_model,
        api_base_digest,
        api_key_digest,
        _resolve_cache_dir(app_config),
    )


def reset_rerank_provider_cache() -> None:
    """Clear the process-level reranker provider cache (used by tests)."""
    with _RERANK_PROVIDER_CACHE_LOCK:
        _RERANK_PROVIDER_CACHE.clear()


def create_rerank_provider(app_config: BasicMemoryConfig) -> RerankProvider | None:
    """Create a reranker provider, or ``None`` when reranking is disabled.

    Returning ``None`` (rather than a no-op provider) keeps the disabled path
    allocation-free and lets the search pipeline skip reranking with a simple
    identity check.
    """
    # Trigger: reranking is opt-in and off by default.
    # Why: a cross-encoder adds latency and a first-run model download; existing
    # users must see zero change until they turn it on.
    # Outcome: no provider, no import of fastembed/litellm rerank paths.
    if not app_config.reranker_enabled:
        return None

    cache_key = _rerank_cache_key(app_config)
    with _RERANK_PROVIDER_CACHE_LOCK:
        if cached_provider := _RERANK_PROVIDER_CACHE.get(cache_key):
            return cached_provider

    provider: RerankProvider
    provider_name = app_config.reranker_provider.strip().lower()
    if provider_name == "fastembed":
        # Deferred import: fastembed (and its onnxruntime dep) may not be installed.
        from basic_memory.repository.fastembed_rerank_provider import FastEmbedRerankProvider

        resolved_threads, _ = _resolve_fastembed_runtime_knobs(app_config)
        provider = FastEmbedRerankProvider(
            model_name=app_config.reranker_model,
            cache_dir=_resolve_cache_dir(app_config),
            threads=resolved_threads,
        )
    elif provider_name == "litellm":
        from basic_memory.repository.litellm_rerank_provider import LiteLLMRerankProvider

        provider = LiteLLMRerankProvider(
            model_name=app_config.reranker_model,
            api_key=app_config.reranker_api_key,
            api_base=app_config.reranker_api_base,
        )
    else:
        raise ValueError(f"Unsupported reranker provider: {provider_name}")

    with _RERANK_PROVIDER_CACHE_LOCK:
        if cached_provider := _RERANK_PROVIDER_CACHE.get(cache_key):
            return cached_provider
        if _RERANK_PROVIDER_CACHE:
            logger.warning(
                "Creating a second distinct reranker provider in this process; "
                "the model will be loaded again. existing_keys={existing} new_key={new}",
                existing=list(_RERANK_PROVIDER_CACHE.keys()),
                new=cache_key,
            )
        _RERANK_PROVIDER_CACHE[cache_key] = provider
        return provider
