"""Factory for creating configured semantic embedding providers."""

from threading import Lock

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider import EmbeddingProvider

type ProviderCacheKey = tuple[str, str, int | None, int, str | None, int | None, int | None]

_EMBEDDING_PROVIDER_CACHE: dict[ProviderCacheKey, EmbeddingProvider] = {}
_EMBEDDING_PROVIDER_CACHE_LOCK = Lock()


def _provider_cache_key(app_config: BasicMemoryConfig) -> ProviderCacheKey:
    """Build a stable cache key from provider-relevant semantic embedding config."""
    return (
        app_config.semantic_embedding_provider.strip().lower(),
        app_config.semantic_embedding_model,
        app_config.semantic_embedding_dimensions,
        app_config.semantic_embedding_batch_size,
        app_config.semantic_embedding_cache_dir,
        app_config.semantic_embedding_threads,
        app_config.semantic_embedding_parallel,
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

        if app_config.semantic_embedding_cache_dir is not None:
            extra_kwargs["cache_dir"] = app_config.semantic_embedding_cache_dir
        if app_config.semantic_embedding_threads is not None:
            extra_kwargs["threads"] = app_config.semantic_embedding_threads
        if app_config.semantic_embedding_parallel is not None:
            extra_kwargs["parallel"] = app_config.semantic_embedding_parallel

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
            **extra_kwargs,
        )
    else:
        raise ValueError(f"Unsupported semantic embedding provider: {provider_name}")

    with _EMBEDDING_PROVIDER_CACHE_LOCK:
        if cached_provider := _EMBEDDING_PROVIDER_CACHE.get(cache_key):
            return cached_provider
        _EMBEDDING_PROVIDER_CACHE[cache_key] = provider
        return provider
