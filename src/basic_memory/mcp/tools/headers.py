from httpx._types import (
    HeaderTypes,
)
from loguru import logger
from fastmcp.server.dependencies import get_http_headers


def inject_auth_header(headers: HeaderTypes | None = None) -> HeaderTypes:
    """
    Inject JWT token from FastMCP context into headers if available.

    Args:
        headers: Existing headers dict or None

    Returns:
        Headers dict with Authorization header added if JWT is available
    """
    # Start with existing headers or empty dict
    if headers is None:
        headers = {}
    elif not isinstance(headers, dict):
        # Convert other header types to dict
        headers = dict(headers)  # type: ignore
    else:
        # Make a copy to avoid modifying the original
        headers = headers.copy()

    http_headers = get_http_headers()

    # Log only non-sensitive header keys for debugging
    if logger.opt(lazy=True).debug:
        sensitive_headers = {"authorization", "cookie", "x-api-key", "x-auth-token", "api-key"}
        safe_headers = {k for k in http_headers.keys() if k.lower() not in sensitive_headers}
        logger.debug(f"HTTP headers present: {list(safe_headers)}")

    authorization = http_headers.get("Authorization") or http_headers.get("authorization")
    if authorization:
        headers["Authorization"] = authorization  # type: ignore
        # Log only that auth was injected, not the token value
        logger.debug("Injected authorization header into request")
    else:
        logger.debug("No authorization header found in request")

    return headers
