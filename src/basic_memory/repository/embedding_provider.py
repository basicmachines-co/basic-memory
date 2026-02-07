"""Embedding provider protocol for pluggable semantic backends."""

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Contract for semantic embedding providers."""

    model_name: str
    dimensions: int

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of document chunks."""
        ...
