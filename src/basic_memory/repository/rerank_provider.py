"""Reranker provider protocol for pluggable cross-encoder rescoring.

A reranker rescores a small candidate set (query + document together via
cross-attention) after retrieval, recovering relevant results that bi-encoder /
FTS ranking left just below the top-k cutoff. Mirrors ``embedding_provider`` so
the same provider families and config shape apply to a different pipeline stage.
"""

from typing import Any, Protocol


class RerankProvider(Protocol):
    """Contract for cross-encoder reranking providers."""

    model_name: str

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return a relevance score in ``[0, 1]`` per document, aligned to input order.

        Higher is more relevant. Providers whose model emits raw logits (e.g. a
        local cross-encoder) must squash to ``[0, 1]`` themselves so the search
        pipeline can treat every reranker's output on one comparable scale and
        keep the public result score bounded.
        """
        ...

    def runtime_log_attrs(self) -> dict[str, Any]:
        """Return provider-specific runtime settings suitable for startup logs."""
        ...
