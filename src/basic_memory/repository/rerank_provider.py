"""Reranker provider protocol for pluggable cross-encoder rescoring.

A reranker rescores a small candidate set (query + document together via
cross-attention) after retrieval, recovering relevant results that bi-encoder /
FTS ranking left just below the top-k cutoff. Mirrors ``embedding_provider`` so
the same provider families and config shape apply to a different pipeline stage.
"""

from typing import Any, Protocol

from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    SemanticDependenciesMissingError,
)

# Permanent reranker faults every rerank call site must surface rather than degrade
# past: missing dependencies and provider-contract breaks are config/provider bugs
# that would otherwise leave reranking silently broken with no signal. Shared so the
# repository pipeline and the MCP merged-pool rerank cannot drift on this taxonomy.
PERMANENT_RERANK_ERRORS = (SemanticDependenciesMissingError, RerankProviderContractError)


def build_rerank_document(title: str | None, body: str | None, max_chars: int) -> str:
    """Assemble the text handed to the cross-encoder for one candidate.

    Prepend the title so short or title-only candidates still carry a usable
    signal. Truncate to ``max_chars`` (when positive) so long notes don't inflate
    cross-encoder latency — the leading text carries the strongest signal.
    """
    title = title or ""
    body = body or ""
    text = f"{title}\n{body}" if (title and body) else (body or title)
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars]
    return text


def demote_tail_scores(floor: float, count: int) -> list[float]:
    """Scores in ``(0, floor)``, descending, for candidates left out of a rerank pool.

    Reranked candidates carry [0, 1] relevance while un-reranked ones still hold
    retrieval scores on a different scale; left as is, a tail candidate could
    numerically outrank a reranked one. Pinning the tail strictly below the
    reranked floor keeps one monotonic, comparable ordering without a second
    provider call.
    """
    return [floor * (count - index) / (count + 1) for index in range(count)]


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
