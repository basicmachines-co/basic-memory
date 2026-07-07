"""Embedding provider wrapper for role-specific literal text prefixes."""

from __future__ import annotations

import json
from typing import Any

from basic_memory.repository.embedding_provider import EmbeddingProvider


def normalize_embedding_prefix(value: str | None) -> str | None:
    """Treat unset and empty prefixes as disabled while preserving meaningful spaces."""
    if value == "":
        return None
    return value


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
        """Return embedding semantics including literal text-prefix transforms."""
        provider_identity_key = getattr(self.provider, "identity_key", None)
        if callable(provider_identity_key):
            provider_identity = provider_identity_key()
        else:
            provider_identity = f"{self.provider.model_name}:{self.provider.dimensions}"

        return (
            f"{type(self.provider).__name__}:{provider_identity}:"
            f"document_prefix={json.dumps(self.document_prefix, ensure_ascii=True)}:"
            f"query_prefix={json.dumps(self.query_prefix, ensure_ascii=True)}"
        )
