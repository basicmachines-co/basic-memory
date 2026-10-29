"""Embedding provider protocol for pluggable semantic backends."""

import math
from typing import Any, Protocol, runtime_checkable


class EmbeddingProvider(Protocol):
    """Contract for semantic embedding providers."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of document chunks."""
        ...

    def runtime_log_attrs(self) -> dict[str, Any]:
        """Return provider-specific runtime settings suitable for startup logs."""
        ...


@runtime_checkable
class EmbeddingIdentityProvider(Protocol):
    """Optional capability for providers with semantics beyond model and dimensions."""

    def identity_key(self) -> str:
        """Return a stable identity for persisted-vector invalidation."""
        ...


def unit_normalized(vector: list[float]) -> list[float]:
    """Scale a vector to length 1; a zero vector stays as it is.

    Every provider must return unit vectors: the SQLite vector index turns L2 distance
    into cosine similarity with ``1 - L²/2``, which holds only for unit vectors. Many
    models and OpenAI-compatible endpoints return vectors of other lengths, and
    unnormalized vectors make every semantic match fall below the similarity floor.
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def embedding_provider_identity(provider: EmbeddingProvider) -> str:
    """Return a provider's explicit semantic identity or the protocol fallback."""
    if isinstance(provider, EmbeddingIdentityProvider):
        return provider.identity_key()
    return f"{provider.model_name}:{provider.dimensions}"
