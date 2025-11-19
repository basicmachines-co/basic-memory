"""API middleware."""

from basic_memory.api.middleware.deprecation import DeprecationMiddleware, DeprecationMetrics

__all__ = ["DeprecationMiddleware", "DeprecationMetrics"]
