"""Deprecation middleware for v1 API endpoints.

This middleware adds deprecation headers to v1 API responses and tracks
usage metrics to help monitor the migration to v2.
"""

from collections import Counter
from datetime import datetime, timedelta

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class DeprecationMetrics:
    """Track v1 and v2 API usage for migration planning."""

    def __init__(self):
        """Initialize metrics counters."""
        self.v1_calls = Counter()
        self.v2_calls = Counter()

    def record_v1_call(self, endpoint: str, client: str | None = None):
        """Record a v1 API call.

        Args:
            endpoint: The endpoint path that was called
            client: Optional client identifier
        """
        self.v1_calls[endpoint] += 1

    def record_v2_call(self, endpoint: str):
        """Record a v2 API call.

        Args:
            endpoint: The endpoint path that was called
        """
        self.v2_calls[endpoint] += 1

    def get_stats(self) -> dict:
        """Get usage statistics.

        Returns:
            Dictionary with v1/v2 call counts and adoption metrics
        """
        total_v1 = sum(self.v1_calls.values())
        total_v2 = sum(self.v2_calls.values())
        total = total_v1 + total_v2

        return {
            "v1_calls": total_v1,
            "v2_calls": total_v2,
            "total_calls": total,
            "v2_adoption_rate": total_v2 / total if total > 0 else 0,
            "top_v1_endpoints": self.v1_calls.most_common(10),
            "top_v2_endpoints": self.v2_calls.most_common(10),
        }


class DeprecationMiddleware(BaseHTTPMiddleware):
    """Add deprecation headers to v1 API responses.

    This middleware:
    - Adds standard deprecation headers to v1 endpoints
    - Logs v1 API usage for monitoring
    - Tracks metrics for v1 and v2 adoption
    - Provides sunset date information
    """

    def __init__(
        self, app, sunset_date: str | None = None, metrics: DeprecationMetrics | None = None
    ):
        """Initialize deprecation middleware.

        Args:
            app: FastAPI application
            sunset_date: ISO 8601 date string for v1 sunset (default: 6 months from now)
            metrics: Optional DeprecationMetrics instance for tracking
        """
        super().__init__(app)
        self.sunset_date = sunset_date or self._calculate_sunset_date()
        self.metrics = metrics or DeprecationMetrics()

    def _calculate_sunset_date(self) -> str:
        """Calculate sunset date 6 months from now.

        Returns:
            HTTP date string for sunset header
        """
        sunset = datetime.now() + timedelta(days=180)
        return sunset.strftime("%a, %d %b %Y 23:59:59 GMT")

    async def dispatch(self, request: Request, call_next):
        """Process request and add deprecation headers to v1 responses.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware in chain

        Returns:
            HTTP response with deprecation headers if applicable
        """
        response = await call_next(request)

        path = request.url.path

        # Check if this is a v2 endpoint
        if path.startswith("/v2"):
            self.metrics.record_v2_call(path)
            return response

        # Check if this is a deprecated v1 endpoint
        if self._is_deprecated_endpoint(path):
            # Add deprecation headers
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = self.sunset_date
            response.headers["Link"] = '</v2>; rel="successor-version"'
            response.headers["X-API-Warn"] = (
                "This API version is deprecated. "
                "Please migrate to /v2 endpoints. "
                f"Support ends: {self.sunset_date}"
            )

            # Record metrics
            self.metrics.record_v1_call(path, request.client.host if request.client else None)

            # Log v1 usage
            logger.warning(
                "V1 API endpoint accessed (deprecated)",
                endpoint=path,
                method=request.method,
                client=request.client.host if request.client else None,
                sunset_date=self.sunset_date,
            )

        return response

    def _is_deprecated_endpoint(self, path: str) -> bool:
        """Check if path is a deprecated v1 endpoint.

        Args:
            path: Request path

        Returns:
            True if this is a v1 endpoint that should show deprecation warnings
        """
        # List of v1 endpoint prefixes that are deprecated
        deprecated_patterns = [
            "/knowledge/",
            "/memory/",
            "/search/",
            "/resource/",
            "/directory/",
            "/prompt/",
        ]

        # Skip non-API paths
        if path.startswith("/docs") or path.startswith("/openapi") or path == "/":
            return False

        # Check if path contains any deprecated prefix
        # (accounting for /{project} prefix in URLs like /myproject/knowledge/entities)
        return any(pattern in path for pattern in deprecated_patterns)
