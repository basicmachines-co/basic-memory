"""Typed errors for semantic search configuration and dependency failures."""


class SemanticSearchDisabledError(RuntimeError):
    """Raised when vector or hybrid retrieval is requested but semantic search is disabled."""


class SemanticDependenciesMissingError(RuntimeError):
    """Raised when a semantic search dependency is unavailable or misconfigured."""
