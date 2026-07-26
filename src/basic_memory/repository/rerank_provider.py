"""Reranker provider protocol for pluggable cross-encoder rescoring.

A reranker rescores a small candidate set (query + document together via
cross-attention) after retrieval, recovering relevant results that bi-encoder /
FTS ranking left just below the top-k cutoff. Mirrors ``embedding_provider`` so
the same provider families and config shape apply to a different pipeline stage.
"""

import math
from collections.abc import Sequence
from typing import Any, Protocol

from basic_memory.repository.semantic_errors import RerankProviderContractError


def build_rerank_document(title: str | None, body: str | None, max_chars: int) -> str:
    """Assemble the text handed to the cross-encoder for one candidate.

    Lead with the matched body because it carries the retrieval signal, then append
    the title so short or title-only candidates still provide context. Truncate to
    ``max_chars`` (when positive) so long notes don't inflate cross-encoder latency.
    """
    title = title or ""
    body = body or ""
    text = f"{body}\n{title}" if (title and body) else (body or title)
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars]
    return text


def demote_tail_scores(floor: float, count: int) -> list[float]:
    """Return bounded tail scores at or below the reranked floor.

    Reranked candidates carry [0, 1] relevance while un-reranked ones still hold
    retrieval scores on a different scale; left as is, a tail candidate could
    numerically outrank a reranked one. Positive floors produce descending scores
    in ``(0, floor)`` based only on the row's tail rank, so fetching more rows does
    not rescale earlier results. A zero floor must remain zero to preserve the public
    [0, 1] contract, so callers preserve the reranked-pool-before-tail ordering
    explicitly instead of relying on a numerically smaller sentinel.
    """
    return [floor / (index + 2) for index in range(count)]


def validate_rerank_scores(scores: Sequence[float | str], expected_count: int) -> list[float]:
    """Return finite scores in ``[0, 1]`` or raise a provider contract error."""
    if len(scores) != expected_count:
        raise RerankProviderContractError(
            f"Reranker returned {len(scores)} scores for {expected_count} documents."
        )

    validated: list[float] = []
    for index, score in enumerate(scores):
        try:
            value = float(score)
        except (TypeError, ValueError) as exc:
            raise RerankProviderContractError(
                f"Reranker score at index {index} is not a number: {score!r}."
            ) from exc
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise RerankProviderContractError(
                f"Reranker score at index {index} must be finite and in [0, 1], got {value!r}."
            )
        validated.append(value)
    return validated


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
