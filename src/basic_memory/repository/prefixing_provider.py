"""Embedding provider wrapper for role-specific literal text prefixes."""

from __future__ import annotations

import hashlib
from typing import Any

from basic_memory.repository.embedding_provider import (
    EmbeddingProvider,
    embedding_provider_identity,
)


def normalize_embedding_prefix(value: str | None) -> str | None:
    """Treat unset and empty prefixes as disabled while preserving meaningful spaces."""
    if value == "":
        return None
    return value


def embedding_prefix_digest(value: str | None) -> str:
    """Return a stable non-secret prefix identity, reserving ``-`` for unset."""
    normalized = normalize_embedding_prefix(value)
    if normalized is None:
        return "-"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class PrefixingEmbeddingProvider(EmbeddingProvider):
    """Apply document/query text prefixes before delegating to an embedding provider."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        document_prefix: str | None = None,
        query_prefix: str | None = None,
    ) -> None:
        self.provider = provider
        self.document_prefix = normalize_embedding_prefix(document_prefix)
        self.query_prefix = normalize_embedding_prefix(query_prefix)

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    @property
    def dimensions(self) -> int:
        return self.provider.dimensions

    async def embed_query(self, text: str) -> list[float]:
        if self.query_prefix is not None:
            text = f"{self.query_prefix}{text}"
        return await self.provider.embed_query(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.document_prefix is not None:
            texts = [f"{self.document_prefix}{text}" for text in texts]
        return await self.provider.embed_documents(texts)

    def runtime_log_attrs(self) -> dict[str, Any]:
        attrs = self.provider.runtime_log_attrs()
        attrs.update(
            {
                "document_prefix_set": self.document_prefix is not None,
                "query_prefix_set": self.query_prefix is not None,
            }
        )
        if self.document_prefix is not None:
            attrs["document_prefix_length"] = len(self.document_prefix)
        if self.query_prefix is not None:
            attrs["query_prefix_length"] = len(self.query_prefix)
        return attrs

    def identity_key(self) -> str:
        """Return embedding semantics without exposing literal prefix content."""
        provider_identity = embedding_provider_identity(self.provider)
        return (
            f"{type(self.provider).__name__}:{provider_identity}:"
            f"document_prefix_sha256={embedding_prefix_digest(self.document_prefix)}:"
            f"query_prefix_sha256={embedding_prefix_digest(self.query_prefix)}"
        )
