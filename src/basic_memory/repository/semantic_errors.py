"""Typed errors for semantic search configuration and dependency failures."""


class SemanticSearchDisabledError(RuntimeError):
    """Raised when vector or hybrid retrieval is requested but semantic search is disabled."""


class SemanticDependenciesMissingError(RuntimeError):
    """Raised when a semantic search dependency is unavailable or misconfigured."""


class RerankProviderContractError(RuntimeError):
    """Raised when a reranker provider violates its response contract.

    A distinct type so the search pipeline can surface this permanent fault (a
    provider/config bug) instead of degrading it to un-reranked results the way it
    handles transient reranker failures.
    """
