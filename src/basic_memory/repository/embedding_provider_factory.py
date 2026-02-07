"""Factory for creating configured semantic embedding providers."""

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.openai_provider import OpenAIEmbeddingProvider


def create_embedding_provider(app_config: BasicMemoryConfig) -> EmbeddingProvider:
    """Create an embedding provider based on semantic config."""
    provider_name = app_config.semantic_embedding_provider.strip().lower()

    if provider_name == "fastembed":
        return FastEmbedEmbeddingProvider(
            model_name=app_config.semantic_embedding_model,
            batch_size=app_config.semantic_embedding_batch_size,
        )

    if provider_name == "openai":
        model_name = app_config.semantic_embedding_model or "text-embedding-3-small"
        if model_name == "bge-small-en-v1.5":
            model_name = "text-embedding-3-small"
        return OpenAIEmbeddingProvider(
            model_name=model_name,
            batch_size=app_config.semantic_embedding_batch_size,
        )

    raise ValueError(f"Unsupported semantic embedding provider: {provider_name}")
